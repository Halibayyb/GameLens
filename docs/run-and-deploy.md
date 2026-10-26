# Run and deploy

## Match sources

`MATCH_SOURCE` picks the match the API serves:

| Value | Match |
|---|---|
| `proto` | The demo: a 90-minute synthetic match, Home 2-1 Away, built around three hand-tuned goals (71', 82', 87'). Used in production |
| `synthetic` (default) | A fully simulated match from `data_gen/synthetic_match.py`; `MATCH_SEED` picks another one |
| `vision` | The output of the vision pipeline for a clip you supply |
| `live` | A stub for a licensed live feed; it raises a clear error |

Goal videos live in `web/public/videos/goal{1,2,3}_full.mp4` (simulated clips, not broadcast footage).
Their source clips are in `media/goal_clips/` (not deployed); `python scripts/merge_goal_videos.py` joins each
goal's three clips into one (needs ffmpeg).

## The admin upload page

```bash
ADMIN_VIDEO=1 ADMIN_TOKEN=pick-a-long-token uvicorn api.main:app --reload
# open /admin/video, sign in as admin with the token, then choose your own password
```

The token is a one-time password: after the first sign-in, a hashed password (stored in Blob Storage on
Azure) takes over. **Use demo clip** runs a 12-second schematic without a GPU.

## Deploy to Azure

Production runs on: Static Web Apps (web), Container Apps (API, and the vision service with 4 vCPU and
8 GiB; both scale to zero), Microsoft Foundry (gpt-5-mini, deployment `scene-vision`), Azure AI Speech and
Translator on the same Foundry resource, Blob Storage, Event Hubs.

```bash
export VITE_API_BASE=$(azd env get-value API_URL)   # baked into the web build
azd deploy api
azd deploy web
azd deploy vision      # builds locally when ACR Tasks are not allowed (Free Trial)
```

- Do **not** run `azd up` or `azd provision` on the live environment: provisioning resets the API and
  vision apps to a placeholder image, and Free Trial subscriptions can refuse new Azure OpenAI accounts.
- The API and the vision app sign in with their **managed identities**. Each needs
  **Cognitive Services User** on the Foundry resource (speech, translation and the model):

  ```bash
  az role assignment create --assignee-object-id <app identity principalId> \
    --assignee-principal-type ServicePrincipal --role "Cognitive Services User" --scope <foundry resource id>
  ```

**Alternative: one VM.** `scripts/azure-vm.sh` creates an Ubuntu VM from `infra/vm.bicep` that runs the
whole app with Docker Compose (`docker compose up --build` does the same locally).

## Settings on the API

| Setting | Purpose |
|---|---|
| `MATCH_SOURCE=proto`, `MATCH_SEED=7` | the demo match |
| `TRANSLATE_LLM_ENDPOINT`, `TRANSLATE_LLM_MODEL` | gpt-5-mini for translation (`https://<resource>.openai.azure.com/openai/v1/`, `scene-vision`) |
| `AZURE_TRANSLATOR_ENDPOINT` | Translator fallback (the resource's custom domain) |
| `AZURE_SPEECH_REGION`, `AZURE_SPEECH_RESOURCE_ID` | the commentary voice |
| `ADMIN_VIDEO=1`, `ADMIN_TOKEN` (secret) | the admin page |
| `VISION_URL` | where uploads are tracked |

The vision app takes `SCENE_LLM_ENDPOINT`, `SCENE_LLM_MODEL=scene-vision` and
`SCENE_LLM_AUTH=entra` for the scene model. Without them the step is skipped. See `.env.example` for
the rest.

## Costs

Almost nothing at demo traffic: about $1 so far this month. Container Apps and Static Web Apps sit on free
allowances, model and speech calls are fractions of a cent per goal and are cached, and Event Hubs (about
$0.70 a month while idle) is the largest line. A serverless GPU for vision (`deployGpu`) would cost far more
per hour; it stays off. `azd down` removes everything.
