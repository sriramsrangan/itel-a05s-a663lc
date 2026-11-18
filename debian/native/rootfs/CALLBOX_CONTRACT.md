# Call box contract

*How HMAS, your-gateway-project and the Debian phone talk to each other.*

The phone (itel A05s, native Debian) is the only device with cellular access. It keeps only outbound HTTPS connections to your-gateway-project. HMAS asks your-gateway-project to place calls; your-gateway-project queues them, renders speech and relays outcomes back to HMAS in the format HMAS already understands. Every SMS the phone receives goes to your-gateway-project, which keeps metadata only and emails the text.

## Parties And Identities

*Who calls whom, and with what credential.*

- HMAS functions (project `your-hmas-project`) call your-gateway-project as `hmas-caller@your-hmas-project.iam.gserviceaccount.com` with a Google OIDC ID token, the same route HMAS already uses for WhatsApp (`hm_service_accounts`).
- The phone calls your-gateway-project with an API key (`Authorization: Bearer hm_...`) whose scope is `callbox`. your-gateway-project stores only its hash. The plaintext lives only in Secret Manager in project `your-gateway-project`, secret `callbox-device-key`, and on the phone in `/etc/callbox/device.key` (mode 600), written at provisioning.
- your-gateway-project relays results and heartbeats to HMAS by publishing to the Pub/Sub topic `projects/your-hmas-project/topics/call-results` as `wa-sender@your-gateway-project.iam.gserviceaccount.com` (needs `roles/pubsub.publisher` on that topic only).
- Base URL for the phone: the `hm-gateway` Cloud Run URL directly (not Firebase Hosting, which cuts requests at 60 seconds). Configurable on the phone in `/etc/callbox/agent.toml`.

## Message Shapes

*Identical field names to HMAS `functions/meetings/src/callbox/messages.ts`.*

- `CallJob`: `requestId` (string), `number` (E.164, `^\+91[6-9]\d{9}$`, or the literal `121` only when `dryRun` is false and `kind` is `test`), `text` (at most 500 characters), `expiresAtMs`, `dryRun` (boolean), `kind` (`edit-request`, `meeting-call` or `test`).
- `CallResult`: `requestId`, `outcome` (`answered`, `notAnswered`, `endedEarly`, `unknown`, `failed`, `refused`, `expired`, `dryRun`), `detail`, `dialledAtMs`, `endedAtMs`, `live`, `spoken`, `agentVersion`, `atMs`. Optional extras that HMAS ignores: `dtmf` (digits heard), `transcript` (downlink speech-to-text).
- `Heartbeat`: as in `messages.ts` (phone status, `speechReady`, `callsToday`, `dailyCap`, and so on). `bluetoothConnected` is always false on the Debian phone; `phone.adb` style fields are reported as the Debian equivalents (modem up, SIM ready, registration, data bearer, Wi-Fi).

## Endpoints On hm-gateway

*All JSON unless stated; all return 401 without the right credential.*

1. `POST /v1/calls` (HMAS OIDC). Body `CallJob`. Validates, renders the speech with Cloud Text-to-Speech (voice `en-AU-Chirp3-HD-Aoede`, LINEAR16, 8000 Hz, mono WAV), stores the job in Firestore `hm_callbox_jobs/{requestId}` with status `queued` and the audio in Cloud Storage or Firestore, and returns `202 {requestId}`. Idempotent on `requestId`. Rejects expired jobs.
2. `GET /v1/callbox/jobs/next?wait=25` (device key). Long poll up to `wait` seconds. Claims at most one `queued`, unexpired job in a Firestore transaction (status `claimed`, never handed out twice) and returns `200 {job: CallJob, audioUrl}`; otherwise `204`. Expired queued jobs are marked `expired` and a `CallResult` with outcome `expired` is relayed.
3. `GET /v1/callbox/audio/{requestId}` (device key). The WAV for that job.
4. `POST /v1/callbox/results` (device key). Body `CallResult`. Stored on the job, then published to HMAS `call-results` unchanged in shape (HMAS fields only).
5. `POST /v1/callbox/heartbeat` (device key). Body `Heartbeat`, published to HMAS `call-results`; the latest one is also kept in `hm_callbox_status/current`.
6. `POST /v1/callbox/recordings/{requestId}` (device key, `audio/wav` 8000 Hz mono). Runs Cloud Speech-to-Text (en-IN, with en-US fallback) and returns `{transcript}`; stores only the transcript on the job, not the audio.
7. `POST /v1/sms/inbound` (device key). Body `{sim: 1|2, simNumber, from, receivedAtMs, text, smsClass}`. Stores metadata only in `hm_sms_inbound` (`sim`, `from`, `receivedAtMs`, `length`, `smsClass`, `emailedAt`), never the text. Emails the full text through the repository's existing mail route to `maintainer@example.com`, `maintainer@example.org`, `teammate@example.com` and `teammate@example.org`, subject `SMS to <simNumber> from <from>`, body as in the Pi's `sms_forward.py` (From, To, Received in IST, blank line, text). Idempotent on `(sim, from, receivedAtMs, sha256(text))` without storing the text (store the hash).
8. `POST /v1/callbox/events` (device key). Body `{type: ussd|stk|class0|cf, sim, atMs, summary}`. Metadata log in `hm_callbox_events`; `summary` is at most 160 characters and must not contain message text.

## Rules

*Limits that every party enforces on its own.*

- Calls only between 08:00 and 21:00 IST, at most 120 a day, 6 a day to one number, none within 15 minutes of the last call to that number, 30-second ring. The phone enforces these even if the server does not, and answers `refused` with a reason.
- No redial. A claimed job ends in exactly one `CallResult`.
- No speech, no call: if the job's audio cannot be fetched or played, the outcome is `failed` with detail `no speech`, unless HMAS sent `dryRun`.
- Tests never call or message real contacts. The only live test number is 121.
