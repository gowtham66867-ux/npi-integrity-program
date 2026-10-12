"""L2-L4: normalize, resolve identity, provider graph, curated tables.

Everything here is a pure function of the staged raw files and the as-of date,
which is what makes any alert re-runnable exactly as of the day it was raised.
"""
from __future__ import annotations

import duckdb
from rapidfuzz import fuzz

from . import reference
from .reference import STREET_ABBREVIATIONS


def has(conn, table: str) -> bool:
    return conn.execute("SELECT (SELECT count(*) FROM duckdb_tables() WHERE table_name = $1) + "
                        "(SELECT count(*) FROM duckdb_views() WHERE view_name = $1)", [table]).fetchone()[0] > 0


def as_of_views(conn, as_of: str):
    """src_<key> = what was published as of the date: latest snapshot, or every cumulative release so far."""
    from .ingest import SOURCES
    for key, src in SOURCES.items():
        conn.execute(f"DROP VIEW IF EXISTS src_{key}")
        if not has(conn, f"stg_{key}"):
            continue
        if src.kind == "snapshot":
            where = (f"_version = (SELECT max(_version) FROM stg_{key} WHERE _version <= DATE '{as_of}')")
        else:
            where = f"_version <= DATE '{as_of}'"
        conn.execute(f"CREATE VIEW src_{key} AS SELECT * FROM stg_{key} WHERE {where}")
        if conn.execute(f"SELECT count(*) FROM src_{key}").fetchone()[0] == 0:
            conn.execute(f"DROP VIEW src_{key}")


def _macros(conn):
    addr = "norm_text(s)"
    for word, abbr in STREET_ABBREVIATIONS.items():
        addr = f"regexp_replace({addr}, '\\b{word}\\b', '{abbr}', 'g')"
    conn.execute(r"""
        CREATE OR REPLACE MACRO norm_text(s) AS
            trim(regexp_replace(regexp_replace(upper(coalesce(s, '')), '[^A-Z0-9 ]', ' ', 'g'), '\s+', ' ', 'g'));
        CREATE OR REPLACE MACRO mdy(s) AS try_strptime(nullif(trim(s), ''), '%m/%d/%Y')::DATE;
        CREATE OR REPLACE MACRO ymd(s) AS
            CASE WHEN coalesce(trim(s), '') IN ('', '00000000') THEN NULL
                 ELSE try_strptime(trim(s), '%Y%m%d')::DATE END;
        CREATE OR REPLACE MACRO zip5(s) AS nullif(left(regexp_replace(coalesce(s, ''), '[^0-9]', '', 'g'), 5), '');
        CREATE OR REPLACE MACRO miles(lat1, lon1, lat2, lon2) AS
            3958.8 * 2 * asin(sqrt(pow(sin(radians(lat2 - lat1) / 2), 2) +
            cos(radians(lat1)) * cos(radians(lat2)) * pow(sin(radians(lon2 - lon1) / 2), 2)));
    """)
    conn.execute(f"CREATE OR REPLACE MACRO norm_addr(s) AS trim(regexp_replace({addr}, '\\s+', ' ', 'g'))")
    conn.execute("CREATE OR REPLACE MACRO addr_key(a1, a2, z) AS norm_addr(concat_ws(' ', a1, a2)) || '|' || zip5(z)")


def _reference(conn):
    conn.execute("CREATE OR REPLACE TABLE ref_taxonomy (code VARCHAR, grp VARCHAR, descr VARCHAR)")
    conn.executemany("INSERT INTO ref_taxonomy VALUES (?, ?, ?)", reference.taxonomy_rows())
    conn.execute("CREATE OR REPLACE TABLE ref_codes (hcpcs VARCHAR, family VARCHAR, minutes INT, amount DOUBLE, descr VARCHAR)")
    conn.executemany("INSERT INTO ref_codes VALUES (?, ?, ?, ?, ?)", reference.code_rows())
    conn.execute("CREATE OR REPLACE TABLE ref_allowed (grp VARCHAR, family VARCHAR)")
    conn.executemany("INSERT INTO ref_allowed VALUES (?, ?)", reference.allowed_rows())
    if has(conn, "src_zip_centroids"):
        conn.execute("""CREATE OR REPLACE TABLE ref_zip AS SELECT DISTINCT zip5(ZIP) zip5, CITY city, STATE state,
                        LAT::DOUBLE lat, LON::DOUBLE lon FROM src_zip_centroids""")
    else:
        conn.execute("CREATE OR REPLACE TABLE ref_zip (zip5 VARCHAR, city VARCHAR, state VARCHAR, lat DOUBLE, lon DOUBLE)")
        conn.executemany("INSERT INTO ref_zip VALUES (?, ?, ?, ?, ?)", reference.zip_rows())


NPPES_SELECT = """
    SELECT "NPI" AS npi, nullif("Entity Type Code", '') AS entity_type,
           norm_text("Provider Organization Name (Legal Business Name)") AS org_name,
           norm_text("Provider Last Name (Legal Name)") AS last_name, norm_text("Provider First Name") AS first_name,
           norm_text("Provider Middle Name") AS middle_name, "Provider Credential Text" AS credential,
           norm_addr("Provider First Line Business Practice Location Address") AS addr1,
           norm_addr("Provider Second Line Business Practice Location Address") AS addr2,
           norm_text("Provider Business Practice Location Address City Name") AS city,
           upper(trim("Provider Business Practice Location Address State Name")) AS state,
           zip5("Provider Business Practice Location Address Postal Code") AS zip5,
           "Provider Business Practice Location Address Telephone Number" AS phone,
           mdy("Provider Enumeration Date") AS enum_date, mdy("Last Update Date") AS last_update,
           mdy("NPI Deactivation Date") AS deact_date, mdy("NPI Reactivation Date") AS react_date,
           norm_text("Authorized Official Last Name") AS auth_last, norm_text("Authorized Official First Name") AS auth_first,
           "Authorized Official Title or Position" AS auth_title, "Healthcare Provider Taxonomy Code_1" AS taxonomy,
           addr_key("Provider First Line Business Practice Location Address",
                    "Provider Second Line Business Practice Location Address",
                    "Provider Business Practice Location Address Postal Code") AS address_key,
           _version, _file, _row, _sha, '{src}' AS src, {rank} AS src_rank
    FROM {table}
"""


def build(conn: duckdb.DuckDBPyConnection, as_of: str):
    as_of_views(conn, as_of)
    _macros(conn)
    _reference(conn)
    A = f"DATE '{as_of}'"

    # ---- NPPES rows (full + weekly), latest profile, deactivations, history (SCD-2) ---------------
    parts = [NPPES_SELECT.format(src="nppes_full", rank=0, table="src_nppes_full")]
    if has(conn, "src_nppes_weekly"):
        parts.append(NPPES_SELECT.format(src="nppes_weekly", rank=1, table="src_nppes_weekly"))
    conn.execute("CREATE OR REPLACE TABLE nppes_rows AS " + " UNION ALL ".join(parts))

    conn.execute(f"""
        CREATE OR REPLACE TABLE providers AS
        WITH ranked AS (
            SELECT *, row_number() OVER (PARTITION BY npi ORDER BY _version DESC, src_rank DESC, _row DESC) rn
            FROM nppes_rows),
        latest AS (SELECT * FROM ranked WHERE rn = 1),
        described AS (
            SELECT *, row_number() OVER (PARTITION BY npi ORDER BY _version DESC, src_rank DESC, _row DESC) rn2
            FROM nppes_rows WHERE entity_type IS NOT NULL)
        SELECT l.npi, d.entity_type, d.org_name, d.last_name, d.first_name, d.middle_name, d.credential,
               d.addr1, d.addr2, d.city, d.state, d.zip5, d.phone, d.enum_date, d.last_update, d.taxonomy,
               coalesce(t.grp, 'OTHER') AS taxonomy_group, coalesce(t.descr, d.taxonomy) AS taxonomy_desc,
               d.auth_last, d.auth_first, d.auth_title, d.address_key,
               l.deact_date, l.react_date,
               CASE WHEN l.deact_date IS NOT NULL AND (l.react_date IS NULL OR l.react_date < l.deact_date)
                    THEN 'Deactivated' ELSE 'Active' END AS status,
               CASE WHEN d.entity_type = '2' THEN d.org_name
                    ELSE trim(concat_ws(' ', d.first_name, d.middle_name, d.last_name)) END AS display_name,
               l._version AS profile_version, l.src AS profile_src
        FROM latest l LEFT JOIN described d ON d.npi = l.npi AND d.rn2 = 1
        LEFT JOIN ref_taxonomy t ON t.code = d.taxonomy
    """)

    deact_parts = ["""SELECT npi, deact_date, src AS source, _version, _row FROM nppes_rows
                      WHERE deact_date IS NOT NULL"""]
    if has(conn, "src_nppes_deactivation"):
        deact_parts.insert(0, """SELECT "NPI" AS npi, mdy("NPPES Deactivation Date") AS deact_date,
                                        'nppes_deactivation' AS source, _version, _row FROM src_nppes_deactivation""")
    conn.execute(f"""
        CREATE OR REPLACE TABLE deactivations AS
        SELECT d.* EXCLUDE (rn),
               CASE WHEN p.react_date > d.deact_date THEN p.react_date END AS react_date  -- closes the window
        FROM (
            SELECT *, row_number() OVER (PARTITION BY npi ORDER BY
                   CASE WHEN source = 'nppes_deactivation' THEN 0 ELSE 1 END, _version DESC) rn
            FROM ({' UNION ALL '.join(deact_parts)}) WHERE deact_date IS NOT NULL) d
        LEFT JOIN providers p USING (npi)
        WHERE rn = 1
    """)

    conn.execute("""
        CREATE OR REPLACE TABLE nppes_history AS
        WITH v AS (
            SELECT npi, _version, src, _row, address_key, taxonomy,
                   concat_ws(' ', auth_first, auth_last) AS official,
                   row_number() OVER (PARTITION BY npi, _version ORDER BY src_rank DESC, _row DESC) rn
            FROM nppes_rows WHERE entity_type IS NOT NULL),
        s AS (SELECT *, lag(address_key) OVER w AS prev_address, lag(taxonomy) OVER w AS prev_taxonomy,
                        lag(official) OVER w AS prev_official, lag(_version) OVER w AS prev_version
              FROM v WHERE rn = 1 WINDOW w AS (PARTITION BY npi ORDER BY _version))
        SELECT npi, _version AS change_date, 'practice address' AS field, prev_address AS old_value,
               address_key AS new_value, src, _row FROM s
               WHERE prev_version IS NOT NULL AND address_key IS DISTINCT FROM prev_address
        UNION ALL
        SELECT npi, _version, 'taxonomy', prev_taxonomy, taxonomy, src, _row FROM s
               WHERE prev_version IS NOT NULL AND taxonomy IS DISTINCT FROM prev_taxonomy
        UNION ALL
        SELECT npi, _version, 'authorized official', prev_official, official, src, _row FROM s
               WHERE prev_version IS NOT NULL AND official IS DISTINCT FROM prev_official AND official <> ''
    """)

    # ---- locations & address reference ----------------------------------------------------------
    sec = ""
    if has(conn, "src_nppes_practice_locations"):
        sec = """UNION ALL
            SELECT "NPI", addr_key("Provider Secondary Practice Location Address- Address Line 1",
                                    "Provider Secondary Practice Location Address-  Address Line 2",
                                    "Provider Secondary Practice Location Address - Postal Code"),
                   zip5("Provider Secondary Practice Location Address - Postal Code"), 'secondary', _version, _row
            FROM src_nppes_practice_locations"""
    conn.execute(f"""
        CREATE OR REPLACE TABLE provider_locations AS
        SELECT l.*, z.lat, z.lon, z.city, z.state FROM (
            SELECT npi, address_key, zip5, 'primary' AS kind, profile_version AS _version, NULL::BIGINT AS _row
            FROM providers WHERE address_key IS NOT NULL {sec}) l
        LEFT JOIN ref_zip z USING (zip5)
    """)
    if has(conn, "src_address_types"):
        conn.execute("""CREATE OR REPLACE TABLE address_types AS
            SELECT addr_key(ADDRESS_LINE_1, ADDRESS_LINE_2, ZIP) AS address_key, ADDRESS_TYPE AS address_type,
                   _version, _row FROM src_address_types""")
    else:
        conn.execute("CREATE OR REPLACE TABLE address_types (address_key VARCHAR, address_type VARCHAR, _version DATE, _row BIGINT)")

    # ---- enrollment, reassignment, order & referring, opt-out -------------------------------------
    if has(conn, "src_pecos_enrollment"):
        conn.execute("""CREATE OR REPLACE TABLE enrollments AS SELECT "NPI" npi, ENRLMT_ID enrollment_id,
            PROVIDER_TYPE_DESC provider_type, STATE_CD state, _version, _row FROM src_pecos_enrollment""")
    else:
        conn.execute("CREATE OR REPLACE TABLE enrollments (npi VARCHAR, enrollment_id VARCHAR, provider_type VARCHAR, state VARCHAR, _version DATE, _row BIGINT)")
    if has(conn, "src_pecos_reassignment"):
        conn.execute("""CREATE OR REPLACE TABLE reassignments AS
            SELECT r."Individual NPI" AS npi, r."Group Enrollment ID" AS group_enrollment_id,
                   norm_text(r."Group Legal Business Name") AS group_name, e.npi AS group_npi, r._version, r._row
            FROM src_pecos_reassignment r LEFT JOIN enrollments e ON e.enrollment_id = r."Group Enrollment ID" """)
    else:
        conn.execute("CREATE OR REPLACE TABLE reassignments (npi VARCHAR, group_enrollment_id VARCHAR, group_name VARCHAR, group_npi VARCHAR, _version DATE, _row BIGINT)")
    if has(conn, "src_order_referring"):
        conn.execute("""CREATE OR REPLACE TABLE order_referring AS SELECT "NPI" npi, PARTB partb, DME dme, HHA hha,
            PMD pmd, HOSPICE hospice, _version, _row FROM src_order_referring""")
    else:
        conn.execute("CREATE OR REPLACE TABLE order_referring (npi VARCHAR, partb VARCHAR, dme VARCHAR, hha VARCHAR, pmd VARCHAR, hospice VARCHAR, _version DATE, _row BIGINT)")
    if has(conn, "src_optout"):
        conn.execute("""CREATE OR REPLACE TABLE optouts AS SELECT "NPI" npi, mdy("Optout Effective Date") eff_date,
            mdy("Optout End Date") end_date, _version, _row FROM src_optout""")
    else:
        conn.execute("CREATE OR REPLACE TABLE optouts (npi VARCHAR, eff_date DATE, end_date DATE, _version DATE, _row BIGINT)")
    if has(conn, "src_medicare_by_provider"):
        conn.execute("""CREATE OR REPLACE TABLE medicare_by_provider AS SELECT Rndrng_NPI npi,
            Rndrng_Prvdr_Type provider_type, Rndrng_Prvdr_State_Abrvtn state, try_cast(Tot_Benes AS INT) benes,
            try_cast(Tot_Srvcs AS DOUBLE) services, try_cast(Tot_Mdcr_Pymt_Amt AS DOUBLE) paid, _version, _row
            FROM src_medicare_by_provider""")
    else:
        conn.execute("CREATE OR REPLACE TABLE medicare_by_provider (npi VARCHAR, provider_type VARCHAR, state VARCHAR, benes INT, services DOUBLE, paid DOUBLE, _version DATE, _row BIGINT)")

    # ---- exclusions (LEIE + SAM), identity resolution ------------------------------------------
    ex = []
    if has(conn, "src_oig_leie"):
        ex.append("""SELECT 'LEIE-' || _version || '-' || _row AS excl_id, 'oig_leie' AS source,
            CASE WHEN norm_text(LASTNAME) <> '' THEN 'individual' ELSE 'entity' END AS kind,
            norm_text(LASTNAME) last_name, norm_text(FIRSTNAME) first_name, norm_text(MIDNAME) middle_name,
            norm_text(BUSNAME) org_name, nullif(nullif(trim(NPI), ''), '0000000000') AS npi,
            upper(STATE) state, zip5(ZIP) zip5, norm_text(CITY) city, EXCLTYPE excl_type, ymd(EXCLDATE) excl_date,
            ymd(REINDATE) rein_date, GENERAL general, SPECIALTY specialty, _version, _row FROM src_oig_leie""")
    if has(conn, "src_sam_exclusions"):
        ex.append("""SELECT 'SAM-' || _version || '-' || _row, 'sam_exclusions',
            CASE WHEN norm_text("Last") <> '' THEN 'individual' ELSE 'entity' END,
            norm_text("Last"), norm_text("First"), norm_text("Middle"), norm_text("Name"),
            nullif(trim("NPI"), ''), upper("State / Province"), zip5("Zip Code"), norm_text("City"),
            "Exclusion Type", mdy("Active Date"), NULL::DATE, "Exclusion Program", "Excluding Agency", _version, _row
            FROM src_sam_exclusions""")
    if ex:
        conn.execute("CREATE OR REPLACE TABLE exclusions AS " + " UNION ALL ".join(ex))
    else:
        conn.execute("""CREATE OR REPLACE TABLE exclusions (excl_id VARCHAR, source VARCHAR, kind VARCHAR,
            last_name VARCHAR, first_name VARCHAR, middle_name VARCHAR, org_name VARCHAR, npi VARCHAR, state VARCHAR,
            zip5 VARCHAR, city VARCHAR, excl_type VARCHAR, excl_date DATE, rein_date DATE, general VARCHAR,
            specialty VARCHAR, _version DATE, _row BIGINT)""")
    resolve_identities(conn)

    # ---- claims (L4 inputs) -----------------------------------------------------------------------
    if has(conn, "src_claims"):
        conn.execute(f"""
            CREATE OR REPLACE TABLE claims AS
            SELECT LINE_ID line_id, CLM_ID claim_id, LINE_NUM::INT line_num, SRVC_DT::DATE service_date,
                   BILLING_NPI billing_npi, RENDERING_NPI rendering_npi, nullif(ORDERING_NPI, '') ordering_npi,
                   BENE_ID patient_id, HCPCS_CD hcpcs, UNITS::INT units, POS_CD pos, zip5(SRVC_ZIP) svc_zip,
                   ALLOWED_AMT::DOUBLE amount, c.family, c.minutes, z.lat, z.lon, z.city svc_city,
                   z.state svc_state, s._file, s._version, s._row
            FROM src_claims s LEFT JOIN ref_codes c ON c.hcpcs = s.HCPCS_CD
            LEFT JOIN ref_zip z ON z.zip5 = zip5(s.SRVC_ZIP)
            WHERE SRVC_DT::DATE <= {A}
        """)
    else:
        conn.execute("""CREATE OR REPLACE TABLE claims (line_id VARCHAR, claim_id VARCHAR, line_num INT,
            service_date DATE, billing_npi VARCHAR, rendering_npi VARCHAR, ordering_npi VARCHAR, patient_id VARCHAR,
            hcpcs VARCHAR, units INT, pos VARCHAR, svc_zip VARCHAR, amount DOUBLE, family VARCHAR, minutes INT,
            lat DOUBLE, lon DOUBLE, svc_city VARCHAR, svc_state VARCHAR, _file VARCHAR, _version DATE, _row BIGINT)""")
    conn.execute("""
        CREATE OR REPLACE VIEW claim_roles AS
        SELECT line_id, billing_npi AS npi, 'billing' AS role, * EXCLUDE (line_id) FROM claims
        UNION ALL SELECT line_id, rendering_npi, 'rendering', * EXCLUDE (line_id) FROM claims
            WHERE rendering_npi <> billing_npi
        UNION ALL SELECT line_id, ordering_npi, 'ordering', * EXCLUDE (line_id) FROM claims
            WHERE ordering_npi IS NOT NULL AND ordering_npi NOT IN (billing_npi, rendering_npi)
    """)
    conn.execute("""
        CREATE OR REPLACE TABLE provider_month AS
        SELECT rendering_npi AS npi, date_trunc('month', service_date)::DATE AS month, count(*) AS lines,
               count(DISTINCT patient_id) AS patients, sum(amount) AS amount
        FROM claims GROUP BY ALL
    """)
    build_graph(conn)


def _name_score(last_a, first_a, mid_a, zip_a, city_a, last_b, first_b, mid_b, zip_b, city_b) -> tuple[float, str]:
    """Explainable match confidence for name-based links (no NPI on the exclusion record)."""
    score = 0.45 if last_a == last_b else 0.45 * fuzz.ratio(last_a, last_b) / 100
    fs = fuzz.ratio(first_a or "", first_b or "") / 100
    short = min(first_a or "", first_b or "", key=len)
    if fs < 1 and first_a and first_b and (first_a.startswith(first_b) or first_b.startswith(first_a)):
        # an initial alone is weak evidence; a real prefix ("JON" / "JONATHAN") is stronger
        fs = 0.5 if len(short) == 1 else max(fs, 0.85)
    score += 0.35 * fs
    basis = ["last name", "first name" if fs == 1 else f"first name ~{fs:.2f}", "state"]
    if zip_a and zip_a == zip_b:
        score += 0.20
        basis.append("ZIP")
    elif city_a and city_a == city_b:
        score += 0.10
        basis.append("city")
    if mid_a and mid_b and mid_a[0] != mid_b[0]:
        score -= 0.10
        basis.append("middle initial conflict")
    return round(min(score, 1.0), 3), " + ".join(basis)


def resolve_identities(conn):
    """Link exclusion records to NPIs (providers) and to authorized officials (people)."""
    conn.execute("""CREATE OR REPLACE TABLE exclusion_matches (excl_id VARCHAR, npi VARCHAR, method VARCHAR,
                    confidence DOUBLE, basis VARCHAR)""")
    conn.execute("""CREATE OR REPLACE TABLE official_matches (excl_id VARCHAR, org_npi VARCHAR, official VARCHAR,
                    confidence DOUBLE, basis VARCHAR)""")
    conn.execute("""INSERT INTO exclusion_matches
        SELECT e.excl_id, e.npi, 'npi', 1.0, 'NPI on exclusion record' FROM exclusions e
        JOIN providers p ON p.npi = e.npi""")
    # Name-based candidates, blocked on last name + state + first initial so it scales to the national file.
    cand = conn.execute("""
        SELECT e.excl_id, p.npi, e.last_name, e.first_name, e.middle_name, e.zip5, e.city,
               p.last_name, p.first_name, p.middle_name, p.zip5, p.city
        FROM exclusions e JOIN providers p
          ON p.entity_type = '1' AND p.last_name = e.last_name AND p.state = e.state
         AND left(p.first_name, 1) = left(e.first_name, 1)
        WHERE e.kind = 'individual' AND e.npi IS NULL
    """).fetchall()
    rows = []
    for r in cand:
        conf, basis = _name_score(*r[2:7], *r[7:12])
        if conf >= 0.6:
            rows.append((r[0], r[1], "name", conf, basis))
    if rows:
        conn.executemany("INSERT INTO exclusion_matches VALUES (?, ?, ?, ?, ?)", rows)

    cand = conn.execute("""
        SELECT e.excl_id, p.npi, e.last_name, e.first_name, e.middle_name, e.zip5, e.city,
               p.auth_last, p.auth_first, '', p.zip5, p.city, p.auth_first || ' ' || p.auth_last
        FROM exclusions e JOIN providers p
          ON p.entity_type = '2' AND p.auth_last = e.last_name AND p.state = e.state
         AND left(p.auth_first, 1) = left(e.first_name, 1)
        WHERE e.kind = 'individual'
    """).fetchall()
    rows = []
    for r in cand:
        conf, basis = _name_score(*r[2:7], *r[7:12])
        if conf >= 0.6:
            rows.append((r[0], r[1], r[12], conf, basis))
    if rows:
        conn.executemany("INSERT INTO official_matches VALUES (?, ?, ?, ?, ?)", rows)

    cand = conn.execute("""
        SELECT e.excl_id, p.npi, e.org_name, p.org_name FROM exclusions e
        JOIN providers p ON p.entity_type = '2' AND p.state = e.state AND left(p.org_name, 3) = left(e.org_name, 3)
        WHERE e.kind = 'entity' AND e.npi IS NULL AND e.org_name <> ''
    """).fetchall()
    rows = [(r[0], r[1], "org name", round(fuzz.token_sort_ratio(r[2], r[3]) / 100, 3), "organization name + state")
            for r in cand if fuzz.token_sort_ratio(r[2], r[3]) >= 92]
    if rows:
        conn.executemany("INSERT INTO exclusion_matches VALUES (?, ?, ?, ?, ?)", rows)


def build_graph(conn):
    """L3 provider graph as an edge table (relational core; recursive SQL / AGE for traversal)."""
    conn.execute("""
        CREATE OR REPLACE TABLE graph_edges AS
        SELECT npi AS src, 'provider' AS src_type, 'practices_at' AS rel, 'ADDR:' || address_key AS dst,
               'address' AS dst_type, kind AS detail FROM provider_locations
        UNION ALL
        SELECT npi, 'provider', 'phone', 'PHONE:' || phone, 'phone', NULL FROM providers
            WHERE coalesce(phone, '') <> ''
        UNION ALL
        SELECT npi, 'provider', 'authorized_official', 'PERSON:' || auth_first || ' ' || auth_last || '|' || state,
               'person', auth_title FROM providers WHERE entity_type = '2' AND coalesce(auth_last, '') <> ''
        UNION ALL
        SELECT 'PERSON:' || m.official || '|' || p.state, 'person', 'excluded_as', m.excl_id, 'exclusion',
               m.basis || ' (' || m.confidence || ')'
            FROM official_matches m JOIN providers p ON p.npi = m.org_npi
        UNION ALL
        SELECT m.npi, 'provider', 'excluded_as', m.excl_id, 'exclusion', m.basis || ' (' || m.confidence || ')'
            FROM exclusion_matches m
        UNION ALL
        SELECT npi, 'provider', 'reassigns_to', coalesce(group_npi, 'ENRL:' || group_enrollment_id),
               CASE WHEN group_npi IS NULL THEN 'enrollment' ELSE 'provider' END, group_name FROM reassignments
        UNION ALL
        SELECT ordering_npi, 'provider', 'orders_for', billing_npi, 'provider', count(*)::VARCHAR || ' lines'
            FROM claims WHERE ordering_npi IS NOT NULL AND ordering_npi <> billing_npi GROUP BY ALL
    """)
