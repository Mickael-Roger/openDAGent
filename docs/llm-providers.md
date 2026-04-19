# LLM Provider Configuration

openDAGent talks to any OpenAI-compatible or Anthropic endpoint. This page shows
ready-to-paste `llm:` blocks for every supported provider and authentication method.

> **Quick reference** — the full list of model features you can declare:
> `vision` · `reasoning` · `json_mode` · `long_context` · `code` · `image_generation` · `native_web_search`
>
> Features gate capabilities at startup: if no configured model advertises `vision`,
> the `analyze_image` capability is hidden automatically.

---

## OpenAI

### API key (standard)

The most common setup. Create an API key at <https://platform.openai.com/api-keys>.

```bash
export OPENAI_API_KEY=sk-...
```

```yaml
llm:
  default_provider: openai
  default_model: gpt-4.1

  providers:
    - id: openai
      type: openai
      endpoint: https://api.openai.com/v1
      auth:
        type: api_key
        env_var: OPENAI_API_KEY
      models:
        - id: gpt-4.1
          role: strong_reasoning
          features: [vision, json_mode, long_context, code]
        - id: gpt-4.1-mini
          role: balanced
          features: [vision, json_mode, code]
        - id: o3
          role: strong_reasoning
          features: [reasoning, json_mode, long_context, code]
        # Native web search via GPT-4o browsing:
        - id: gpt-4o-search-preview
          role: balanced
          features: [vision, json_mode, native_web_search]
        # Image generation:
        - id: gpt-image-1
          role: image_generation
          features: [image_generation]
```

### ChatGPT subscription vs. API credits

A **ChatGPT Plus / Pro / Team subscription** does **not** grant API access — the two
are billed separately. The API requires credits purchased at
<https://platform.openai.com/settings/organization/billing>.

If you have a ChatGPT Pro subscription and want to use `o1` or `o3` via the API,
you still need to add API credits; the subscription only covers the ChatGPT web/app
interface.

### Azure OpenAI

Azure deployments have a per-deployment endpoint and use a different auth header.
Create a deployment in the [Azure portal](https://portal.azure.com) and collect:
- **Endpoint** — e.g. `https://my-resource.openai.azure.com`
- **Deployment name** — the name you chose when creating the deployment
- **API key** — from the resource's "Keys and Endpoint" page

```bash
export AZURE_OPENAI_API_KEY=...
```

```yaml
llm:
  default_provider: azure
  default_model: gpt-4-1

  providers:
    - id: azure
      type: openai                       # Azure uses the OpenAI wire format
      endpoint: https://MY-RESOURCE.openai.azure.com/openai/deployments/MY-DEPLOY
      auth:
        type: api_key
        env_var: AZURE_OPENAI_API_KEY
      models:
        - id: gpt-4-1                    # must match your deployment name
          role: strong_reasoning
          features: [vision, json_mode, long_context, code]
```

> **Note:** Append `?api-version=2024-12-01-preview` to the endpoint if the
> request is rejected due to a missing `api-version` parameter.

---

## Anthropic

Create an API key at <https://console.anthropic.com/settings/keys>.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

```yaml
llm:
  default_provider: anthropic
  default_model: claude-sonnet-4-6

  providers:
    - id: anthropic
      type: anthropic
      endpoint: https://api.anthropic.com
      auth:
        type: api_key
        env_var: ANTHROPIC_API_KEY
      models:
        - id: claude-opus-4-6
          role: strong_reasoning
          features: [vision, reasoning, long_context, code, json_mode]
        - id: claude-sonnet-4-6
          role: balanced
          features: [vision, long_context, code, json_mode]
        - id: claude-haiku-4-5
          role: cheap_fast
          features: [vision, json_mode]
```

---

## Google Gemini

### AI Studio (personal / developer use)

Get a free API key at <https://aistudio.google.com/apikey>.
Gemini exposes an OpenAI-compatible endpoint, so use `type: openai`.

```bash
export GOOGLE_API_KEY=AIza...
```

```yaml
llm:
  default_provider: google
  default_model: gemini-2.5-pro

  providers:
    - id: google
      type: openai
      endpoint: https://generativelanguage.googleapis.com/v1beta/openai
      auth:
        type: api_key
        env_var: GOOGLE_API_KEY
      models:
        - id: gemini-2.5-pro
          role: strong_reasoning
          features: [vision, reasoning, long_context, code, json_mode, native_web_search]
        - id: gemini-2.5-flash
          role: balanced
          features: [vision, long_context, code, json_mode]
        - id: gemini-2.0-flash-lite
          role: cheap_fast
          features: [vision, json_mode]
        # Imagen 3 for image generation:
        # - id: imagen-3.0-generate-002
        #   role: image_generation
        #   features: [image_generation]
```

### Vertex AI (enterprise / GCP)

Vertex AI uses a different base URL and OAuth 2.0 authentication via a service account.

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
# or use gcloud auth application-default login for local dev
```

Set the endpoint to your project's Vertex AI gateway:

```yaml
llm:
  default_provider: vertex
  default_model: gemini-2.5-pro

  providers:
    - id: vertex
      type: openai
      endpoint: https://us-central1-aiplatform.googleapis.com/v1beta1/projects/MY-PROJECT/locations/us-central1/endpoints/openapi
      auth:
        type: none          # auth is handled by the Google SDK via env credentials
      models:
        - id: google/gemini-2.5-pro
          role: strong_reasoning
          features: [vision, reasoning, long_context, code, json_mode]
```

---

## Mistral

Create an API key at <https://console.mistral.ai/api-keys>.

```bash
export MISTRAL_API_KEY=...
```

```yaml
llm:
  default_provider: mistral
  default_model: mistral-large-latest

  providers:
    - id: mistral
      type: openai
      endpoint: https://api.mistral.ai/v1
      auth:
        type: api_key
        env_var: MISTRAL_API_KEY
      models:
        - id: mistral-large-latest
          role: strong_reasoning
          features: [vision, json_mode, code]
        - id: mistral-small-latest
          role: balanced
          features: [json_mode, code]
        - id: codestral-latest
          role: balanced
          features: [code, json_mode]
        - id: pixtral-large-latest
          role: strong_reasoning
          features: [vision, json_mode, long_context]
```

---

## MiniMax

MiniMax (<https://www.minimaxi.com>) provides frontier models with an
OpenAI-compatible API. Get an API key from the [developer console](https://platform.minimaxi.com).

```bash
export MINIMAX_API_KEY=...
```

```yaml
llm:
  default_provider: minimax
  default_model: MiniMax-Text-01

  providers:
    - id: minimax
      type: openai
      endpoint: https://api.minimaxi.com/v1
      auth:
        type: api_key
        env_var: MINIMAX_API_KEY
      models:
        - id: MiniMax-Text-01
          role: strong_reasoning
          features: [vision, long_context, json_mode, code]
        - id: MiniMax-M1
          role: strong_reasoning
          features: [vision, reasoning, long_context, code, json_mode]
```

---

## Zhipu AI (GLM / Z.AI)

Zhipu AI (<https://open.bigmodel.cn>) publishes the GLM-4 family with an
OpenAI-compatible endpoint. Get an API key from the
[Zhipu open platform](https://open.bigmodel.cn/usercenter/apikeys).

```bash
export ZHIPU_API_KEY=...
```

```yaml
llm:
  default_provider: zhipu
  default_model: glm-4-plus

  providers:
    - id: zhipu
      type: openai
      endpoint: https://open.bigmodel.cn/api/paas/v4
      auth:
        type: api_key
        env_var: ZHIPU_API_KEY
      models:
        - id: glm-4-plus
          role: strong_reasoning
          features: [vision, json_mode, long_context, code]
        - id: glm-4-flash
          role: cheap_fast
          features: [json_mode, code]
        - id: glm-4v-plus
          role: balanced
          features: [vision, json_mode]
        # Image generation via CogView:
        # - id: cogview-3-plus
        #   role: image_generation
        #   features: [image_generation]
```

---

## Local / self-hosted

Any server that speaks the OpenAI wire format works. Set `auth.type: none` and
point `endpoint` at your local server.

### Ollama

```bash
ollama pull qwen2.5-coder:32b
ollama serve          # starts on http://localhost:11434 by default
```

```yaml
llm:
  default_provider: local
  default_model: qwen2.5-coder:32b

  providers:
    - id: local
      type: openai
      endpoint: http://localhost:11434/v1
      auth:
        type: none
      models:
        - id: qwen2.5-coder:32b
          role: strong_reasoning
          features: [code, json_mode, long_context]
        - id: llama3.3:70b
          role: balanced
          features: [json_mode]
        - id: gemma3:27b
          role: balanced
          features: [vision, json_mode]
```

### vLLM

```bash
vllm serve Qwen/Qwen2.5-Coder-32B-Instruct --port 8000
```

```yaml
llm:
  default_provider: local
  default_model: Qwen/Qwen2.5-Coder-32B-Instruct

  providers:
    - id: local
      type: openai
      endpoint: http://localhost:8000/v1
      auth:
        type: none
      models:
        - id: Qwen/Qwen2.5-Coder-32B-Instruct
          role: strong_reasoning
          features: [code, json_mode, long_context]
```

### LM Studio

Start the local server from the LM Studio UI (default port 1234).

```yaml
llm:
  default_provider: local
  default_model: my-loaded-model

  providers:
    - id: local
      type: openai
      endpoint: http://localhost:1234/v1
      auth:
        type: none
      models:
        - id: my-loaded-model    # whatever model name LM Studio reports
          role: balanced
          features: [json_mode]
```

---

## Mixing multiple providers

You can declare several providers at once. openDAGent uses `default_provider` /
`default_model` for planning and chat; individual capabilities can override this.
The feature union across all models determines which capabilities are activated.

```yaml
llm:
  default_provider: anthropic
  default_model: claude-sonnet-4-6

  providers:
    - id: anthropic
      type: anthropic
      endpoint: https://api.anthropic.com
      auth:
        type: api_key
        env_var: ANTHROPIC_API_KEY
      models:
        - id: claude-opus-4-6
          role: strong_reasoning
          features: [vision, reasoning, long_context, code, json_mode]
        - id: claude-sonnet-4-6
          role: balanced
          features: [vision, long_context, code, json_mode]

    - id: openai
      type: openai
      endpoint: https://api.openai.com/v1
      auth:
        type: api_key
        env_var: OPENAI_API_KEY
      models:
        - id: gpt-image-1
          role: image_generation
          features: [image_generation]      # unlocks generate_image capability

    - id: local
      type: openai
      endpoint: http://localhost:11434/v1
      auth:
        type: none
      models:
        - id: llama3.3:70b
          role: cheap_fast
          features: [json_mode]
```

---

## Image generation environment variables

The `generate_image` capability does not use the `providers` block for its actual
HTTP call — it reads three dedicated environment variables so you can point it at
any diffusion API independently of the conversational models:

```bash
export IMAGE_GEN_ENDPOINT=https://api.openai.com/v1       # or any compatible API
export IMAGE_GEN_MODEL=gpt-image-1
export IMAGE_GEN_API_KEY=sk-...
```

Declare a model with `features: [image_generation]` in any provider block to
activate the capability gate; the env vars handle the actual request.

---

## Web search environment variables

The `web_search` capability is activated if **either** condition is true:

| Condition | What it enables |
|---|---|
| `BRAVE_API_KEY` is set | Brave Search API — explicit web queries |
| Any model has `native_web_search` feature | Model's built-in browsing (Gemini, GPT-4o search, …) |

```bash
export BRAVE_API_KEY=BSA...    # optional — only needed for Brave search
```
