"""Release harness: independent test process + complete offline eval gates.

Exit non-zero on either failure and save reviewable results. Never spends money
or modifies a cloud project. Billing preflight is a separate mandatory gate.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    output = ROOT / 'release_reports' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output.mkdir(parents=True, exist_ok=True)
    checks = []
    for name, args in [('tests', ['-m', 'pytest', '-q', 'tests', '--junitxml=' + str(output / 'tests.xml')]),
                       ('evaluations', ['-m', 'evals.run_evals'])]:
        with (output / (name + '.log')).open('w') as log:
            print('Running ' + name, flush=True)
            result = subprocess.run([sys.executable, *args], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        checks.append({'name': name, 'passed': result.returncode == 0, 'log': str(output / (name + '.log'))})
    status = all(c['passed'] for c in checks)
    report = {'passed': status, 'checks': checks, 'limits': [
        'Offline synthetic evaluations do not establish real-world accuracy.',
        'Live LLM and deployed Firestore integration require separate verification.',
        'Deployment additionally requires verified billing preflight and authenticated smoke tests.']}
    (output / 'release.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if status else 1

if __name__ == '__main__':
    sys.exit(main())
