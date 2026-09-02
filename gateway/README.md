# Bifrost — the LLM gateway in front of every mode

All three retrieval modes talk to one OpenAI-compatible endpoint instead of to a
provider SDK, so the model is a runtime choice rather than a deployment choice.

    app/llm.py  ->  http://127.0.0.1:8080/v1/chat/completions  ->  DeepSeek | Bedrock

## Why a gateway at all

The comparison only means something if all three modes use the *same* model with
the *same* settings. With one endpoint that is enforced by construction: the
model id is a request parameter and every mode reads it from the same place.
Swapping DeepSeek for Claude or Nova is then a string change, not a code change.

## Credentials

**Bedrock uses no keys.** `bedrock_key_config` carries only a region, so Bifrost
falls back to the AWS SDK default chain and picks up the EC2 instance profile.
The instance role grants `bedrock:InvokeModel` / `Converse` (see `infra/ec2.tf`).
Nothing to rotate, nothing to leak.

**DeepSeek needs its key**, because it is not an AWS service. Bifrost reads
`env.DEEPSEEK_API_KEY`, which docker-compose passes from `/etc/graphrag/graphrag.env`.

To change it:

    aws ssm put-parameter --name /graphrag/demo/deepseek_api_key \
      --type SecureString --value 'sk-...' --overwrite

    aws ssm send-command --instance-ids <id> --document-name AWS-RunShellScript \
      --parameters 'commands=["/opt/graphrag/refresh-env.sh","docker restart graphrag-bifrost","systemctl restart graphrag-api"]'

## Adding another provider

Add a block to `providers` in `config.json`, add its key to the env file, then
list the models in `app/models.py` so the UI offers them. For example OpenAI:

    "openai": { "keys": [{ "name": "openai", "value": "env.OPENAI_API_KEY",
                           "models": ["*"], "weight": 1.0 }] }

Bifrost supports openai, anthropic, azure, bedrock, vertex, gemini, mistral,
groq, cohere, perplexity, xai, cerebras, deepseek, openrouter, nebius,
fireworks, parasail, huggingface, replicate, ollama, vllm, sgl.

## Model ids

Bifrost addresses models as `provider/model`:

    deepseek/deepseek-v4-flash
    bedrock/apac.anthropic.claude-3-5-sonnet-20241022-v2:0
    bedrock/global.amazon.nova-2-lite-v1:0

## The benchmark

Every published number was produced with `deepseek/deepseek-v4-flash`. Switching
models in the playground changes the answers but not the recorded benchmark, and
the UI says so wherever a model can be picked.

## Deploying a change

    scripts/deploy_app.sh --gateway

That packages `app/` plus this directory, uploads to S3, and drives the host over
SSM: extract, rsync into `/opt/graphrag/app/`, copy `config.json` into the
gateway data dir, restart Bifrost and `graphrag-api`, then health-check both.
Omit `--gateway` to ship only the Python.
