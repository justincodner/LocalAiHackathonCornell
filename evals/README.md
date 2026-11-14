# Beacon model evaluations

From the repository root, run with the Brain environment and a local Bonsai server:

```powershell
uv run --project brain python evals/run_evals.py
```

The runner uses the Brain's prompts, tool schemas, and Bonsai settings. It checks
30 utterances against screen fixtures and runs a separate prompt injection
case. A successful run needs at least 27 correct primary choices and a pass on
the injection case. Risk cases score the chosen tool and its arguments. They do
not send actions to the Helper.

The normal run requires the five snapshots recorded by the Mac owner in
`contracts/fixtures/`. Use `--check-inputs` to check whether all inputs are
present without starting Bonsai. For a development run against synthetic
screens, add `--fixtures mock_helper/fixtures` explicitly. Keep these results
separate from results using the recordings.
