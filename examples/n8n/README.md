# n8n examples

## Importable workflow

Import `select-rows-and-email-with-brevo.workflow.json` from the n8n workflow import menu.

After importing:

1. Open **Select rows from IBM i**.
2. Select or create an n8n **Header Auth** credential:
   - Header name: `X-API-Key`
   - Header value: the same value configured as `DB2_API_KEY` in the agent `.env`.
3. Replace `MYLIB.CUSTOMERS` and its selected columns with a fully qualified table allowed by `SQL_ALLOWED_READ_SCHEMAS`.
4. Open **Send email with Brevo** and select your Brevo API credential.
5. Replace `sender@example.com` with a sender authorized in Brevo and set the recipient address.
6. Execute the workflow manually. Replace the Manual Trigger with a Schedule Trigger when periodic delivery is required.

The workflow intentionally contains no API keys or passwords.
