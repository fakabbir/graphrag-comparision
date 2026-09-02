"""The models the playground offers, and which one the benchmark used.

Every published number in this project was produced with BENCHMARK_MODEL. The
others are here so a reader can see how the same retrieval architecture behaves
on a different model - not to re-open the comparison, which would need all 180
attempts re-run per model.

Ids are Bifrost's `provider/model` form. Bedrock ids beginning `apac.` or
`global.` are inference profiles, which route across regions; a bare id like
`deepseek.v3.2` is a regional foundation model in ap-south-1.
"""
from __future__ import annotations

BENCHMARK_MODEL = "deepseek/deepseek-v4-flash"

MODELS: list[dict] = [
    {
        "id": "deepseek/deepseek-v4-flash",
        "label": "DeepSeek V4 Flash",
        "provider": "DeepSeek",
        "note": "Every benchmark number on this site was measured with this model.",
        "benchmark": True,
    },
    {
        "id": "bedrock/global.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "label": "Claude Sonnet 4.5",
        "provider": "Bedrock",
        "note": "Strong at following the two-stage GraphRAG plan format.",
    },
    {
        "id": "bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0",
        "label": "Claude Haiku 4.5",
        "provider": "Bedrock",
        "note": "Fast and cheap; a good check on whether a win needs a large model.",
    },
    {
        "id": "bedrock/apac.anthropic.claude-3-5-sonnet-20241022-v2:0",
        "label": "Claude 3.5 Sonnet",
        "provider": "Bedrock",
        "note": "Older generation, useful as a floor.",
    },
    {
        "id": "bedrock/global.amazon.nova-2-lite-v1:0",
        "label": "Amazon Nova 2 Lite",
        "provider": "Bedrock",
        "note": "Cheapest option here; expect weaker SQL and Cypher.",
    },
    {
        "id": "bedrock/deepseek.v3.2",
        "label": "DeepSeek V3.2 (Bedrock)",
        "provider": "Bedrock",
        "note": "Not the benchmarked model - a different DeepSeek version, served by AWS.",
    },
    {
        "id": "bedrock/qwen.qwen3-coder-480b-a35b-v1:0",
        "label": "Qwen3 Coder 480B",
        "provider": "Bedrock",
        "note": "Code-specialised, so a fair test of LLM-authored SQL and Cypher.",
    },
    {
        "id": "bedrock/openai.gpt-oss-120b-1:0",
        "label": "GPT-OSS 120B",
        "provider": "Bedrock",
        "note": "Open-weight model served on Bedrock.",
    },
]

BY_ID = {m["id"]: m for m in MODELS}


def resolve(model: str | None) -> str:
    """Fall back to the benchmarked model rather than trusting client input.

    The id reaches Bifrost as a request parameter, so an unvalidated value would
    let a caller route to any provider the gateway happens to have configured.
    """
    if not model:
        return BENCHMARK_MODEL
    return model if model in BY_ID else BENCHMARK_MODEL
