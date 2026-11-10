# portfolio-copilot

Backend "brains" of an assistant that answers multi-step questions across a **synthetic** portfolio of
construction projects: permission-filtered hybrid retrieval, an explicit tool-calling loop with streamed,
validated citations, governed text-to-SQL, a model gateway with budgets, and a deterministic eval.

Work in progress — see `docs/PHASE1_PLAN.md`. All data is synthetic. The Terraform in `infra/` is
validated but **not deployed**. No number in this repository is a claim about production usage.

## Quickstart

    cp .env.example .env            # add GROQ_API_KEY for live answers; offline eval needs none
    docker compose up -d --build
    make seed
    make ask Q="What is the largest approved change order on P1?" USER_EMAIL=alice@example.test
    make eval-offline
