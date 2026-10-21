# NPI Integrity Program deployment instructions

- Authenticate Google Cloud commands only as `gowtham@texmed.us`.
- Deploy the NPI console only to project `texmed-npi-integrity` in `us-central1`, under the `texmed.pro` organization (`878199198137`).
- Use the Cloud Billing account linked to that project, currently `01B53D-1B9A75-6E585F`. Verify the live project billing link, open account, organization, project ancestry, and project-scoped budget before deployment. Never switch the project to another billing account automatically.
- The user authorizes billing through whichever payment method is associated with this TexMed Cloud Billing account. Do not require a particular card's last four digits for Google Cloud deployments, and do not store card details in the repository.
- Run the release checks and deployment preflight. Keep Cloud Run private behind IAP and in synthetic data mode; complete authenticated smoke checks after deployment. Do not claim HIPAA compliance or admit PHI into this pilot.
- This instruction concerns Google Cloud billing for this project. Separate TypeSafe/Jev billing has its own policy.
