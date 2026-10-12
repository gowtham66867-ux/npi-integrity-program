"""Reference tables: taxonomy groups, procedure-code families, geography.

These are small, curated lookups loaded into DuckDB on every build. Production
replaces the ZIP table with the Census ZCTA gazetteer and extends the taxonomy
crosswalk to the full NUCC list; the shape stays the same.
"""
from __future__ import annotations

# NUCC taxonomy code -> (group, description)
TAXONOMY = {
    "207R00000X": ("IM", "Internal Medicine"),
    "207Q00000X": ("FM", "Family Medicine"),
    "207RC0000X": ("CARD", "Cardiovascular Disease"),
    "207V00000X": ("OBGYN", "Obstetrics & Gynecology"),
    "2084N0400X": ("NEURO", "Neurology"),
    "2084P0800X": ("PSYCH", "Psychiatry"),
    "103T00000X": ("PSYCHOLOGIST", "Psychologist"),
    "225100000X": ("PT", "Physical Therapist"),
    "207X00000X": ("ORTHO", "Orthopaedic Surgery"),
    "207N00000X": ("DERM", "Dermatology"),
    "363LF0000X": ("NP", "Nurse Practitioner, Family"),
    "363A00000X": ("PA", "Physician Assistant"),
    "111N00000X": ("CHIRO", "Chiropractor"),
    "251E00000X": ("HHA", "Home Health Agency"),
    "193200000X": ("GROUP", "Multi-Specialty Group"),
    "261QM1300X": ("GROUP", "Multi-Specialty Clinic/Center"),
}

# Procedure-code families each taxonomy group is expected to bill.
ALLOWED_FAMILIES = {
    "IM": ["EM", "PRIMARY", "ECG", "RPM"],
    "FM": ["EM", "PRIMARY", "ECG", "RPM"],
    "NP": ["EM", "PRIMARY", "ECG", "RPM"],
    "PA": ["EM", "PRIMARY", "ECG", "RPM", "ORTHO"],
    "CARD": ["EM", "CARD", "ECG", "RPM"],
    "OBGYN": ["EM", "OBGYN", "PRIMARY", "RPM"],
    "NEURO": ["EM", "NEURO", "RPM"],
    "PSYCH": ["EM", "PSYCH"],
    "PSYCHOLOGIST": ["PSYCH"],
    "PT": ["PT"],
    "ORTHO": ["EM", "ORTHO"],
    "DERM": ["EM", "DERM"],
    "CHIRO": ["EM", "CHIRO"],
    "HHA": ["HH"],
}

# HCPCS/CPT -> (family, typical face-to-face minutes per unit or None, typical allowed $, description)
# Minutes are only set for codes billed per encounter day. RPM management codes
# (99457/99458) cover a calendar month, so they never count toward a single day.
CODES = {
    "99202": ("EM", 15, 75, "Office visit, new, straightforward"),
    "99203": ("EM", 30, 110, "Office visit, new, low"),
    "99204": ("EM", 45, 165, "Office visit, new, moderate"),
    "99205": ("EM", 60, 210, "Office visit, new, high"),
    "99211": ("EM", 5, 25, "Office visit, established, minimal"),
    "99212": ("EM", 10, 55, "Office visit, established, straightforward"),
    "99213": ("EM", 20, 90, "Office visit, established, low"),
    "99214": ("EM", 30, 128, "Office visit, established, moderate"),
    "99215": ("EM", 40, 180, "Office visit, established, high"),
    "99453": ("RPM", None, 19, "RPM device setup and patient education"),
    "99454": ("RPM", None, 47, "RPM device supply, each 30 days"),
    "99457": ("RPM", None, 48, "RPM treatment management, first 20 min / month"),
    "99458": ("RPM", None, 38, "RPM treatment management, addl 20 min / month"),
    "36415": ("PRIMARY", None, 3, "Venipuncture"),
    "81002": ("PRIMARY", None, 4, "Urinalysis, non-automated"),
    "90471": ("PRIMARY", None, 17, "Immunization administration"),
    "G0439": ("PRIMARY", 30, 118, "Annual wellness visit, subsequent"),
    "93000": ("ECG", None, 17, "Electrocardiogram, complete"),
    "93306": ("CARD", None, 210, "Echocardiography, complete"),
    "93350": ("CARD", None, 230, "Stress echocardiography"),
    "93880": ("CARD", None, 180, "Duplex scan, extracranial arteries"),
    "59400": ("OBGYN", None, 2100, "Routine obstetric care"),
    "76805": ("OBGYN", None, 125, "OB ultrasound"),
    "58300": ("OBGYN", None, 75, "IUD insertion"),
    "57454": ("OBGYN", None, 140, "Colposcopy with biopsy"),
    "95907": ("NEURO", None, 95, "Nerve conduction studies, 1-2"),
    "95910": ("NEURO", None, 190, "Nerve conduction studies, 7-8"),
    "95913": ("NEURO", None, 260, "Nerve conduction studies, 13+"),
    "95886": ("NEURO", None, 95, "Needle EMG, complete"),
    "95816": ("NEURO", None, 280, "EEG, awake and drowsy"),
    "90837": ("PSYCH", 60, 150, "Psychotherapy, 60 min"),
    "90834": ("PSYCH", 45, 102, "Psychotherapy, 45 min"),
    "90832": ("PSYCH", 30, 76, "Psychotherapy, 30 min"),
    "90791": ("PSYCH", 60, 175, "Psychiatric diagnostic evaluation"),
    "97110": ("PT", 15, 29, "Therapeutic exercise, each 15 min"),
    "97140": ("PT", 15, 27, "Manual therapy, each 15 min"),
    "97530": ("PT", 15, 33, "Therapeutic activities, each 15 min"),
    "97161": ("PT", 20, 100, "PT evaluation, low complexity"),
    "20610": ("ORTHO", None, 62, "Arthrocentesis, major joint"),
    "73560": ("ORTHO", None, 32, "X-ray knee, 1-2 views"),
    "29881": ("ORTHO", None, 520, "Knee arthroscopy, meniscectomy"),
    "11102": ("DERM", None, 105, "Tangential skin biopsy"),
    "17000": ("DERM", None, 68, "Destruction premalignant lesion"),
    "17110": ("DERM", None, 112, "Destruction benign lesions"),
    "98940": ("CHIRO", 15, 30, "Chiropractic manipulation, 1-2 regions"),
    "G0299": ("HH", None, 160, "Home health skilled nursing visit (RN)"),
    "G0151": ("HH", None, 180, "Home health PT visit"),
}

TELEHEALTH_POS = ("02", "10")
IN_PERSON_POS = ("11", "12", "22")

# City -> (state, [(zip, lat, lon)])  synthetic stand-in for the Census ZCTA gazetteer
CITIES = {
    "PHOENIX": ("AZ", [("85004", 33.4510, -112.0687), ("85016", 33.5091, -112.0303)]),
    "TUCSON": ("AZ", [("85719", 32.2471, -110.9472)]),
    "NEWARK": ("NJ", [("07102", 40.7357, -74.1724), ("07103", 40.7384, -74.1953)]),
    "TRENTON": ("NJ", [("08608", 40.2196, -74.7650)]),
    "HOUSTON": ("TX", [("77002", 29.7569, -95.3624), ("77030", 29.7079, -95.4013)]),
    "DALLAS": ("TX", [("75201", 32.7876, -96.7994), ("75246", 32.7942, -96.7728)]),
    "EL PASO": ("TX", [("79901", 31.7587, -106.4869)]),
    "MIAMI": ("FL", [("33130", 25.7680, -80.2048), ("33136", 25.7864, -80.2044)]),
    "TAMPA": ("FL", [("33602", 27.9517, -82.4588)]),
    "JACKSONVILLE": ("FL", [("32202", 30.3299, -81.6516)]),
    "NEW YORK": ("NY", [("10016", 40.7459, -73.9781), ("10032", 40.8382, -73.9426)]),
    "BUFFALO": ("NY", [("14203", 42.8668, -78.8649)]),
    "LOS ANGELES": ("CA", [("90012", 34.0614, -118.2385), ("90033", 34.0505, -118.2117)]),
    "SAN DIEGO": ("CA", [("92103", 32.7466, -117.1696)]),
    "SAN FRANCISCO": ("CA", [("94110", 37.7486, -122.4184)]),
    "CHICAGO": ("IL", [("60612", 41.8805, -87.6873), ("60611", 41.8949, -87.6227)]),
    "ATLANTA": ("GA", [("30303", 33.7525, -84.3915)]),
    "SAVANNAH": ("GA", [("31401", 32.0749, -81.0927)]),
}

STREET_ABBREVIATIONS = {
    "STREET": "ST", "AVENUE": "AVE", "BOULEVARD": "BLVD", "ROAD": "RD", "DRIVE": "DR",
    "PARKWAY": "PKWY", "SUITE": "STE", "BUILDING": "BLDG", "NORTH": "N", "SOUTH": "S",
    "EAST": "E", "WEST": "W", "LANE": "LN", "COURT": "CT", "PLACE": "PL", "HIGHWAY": "HWY",
    "FLOOR": "FL", "APARTMENT": "APT", "UNIT": "UNIT", "NUMBER": "", "NO": "",
}


def taxonomy_rows():
    return [(code, grp, desc) for code, (grp, desc) in TAXONOMY.items()]


def code_rows():
    return [(c, fam, mins, amt, desc) for c, (fam, mins, amt, desc) in CODES.items()]


def zip_rows():
    out = []
    for city, (state, zips) in CITIES.items():
        for z, lat, lon in zips:
            out.append((z, city, state, lat, lon))
    return out


def allowed_rows():
    return [(g, f) for g, fams in ALLOWED_FAMILIES.items() for f in fams]
