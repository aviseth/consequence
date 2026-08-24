# Examples

Everything here operates on a throwaway project in `examples/demo`, rebuilt from
scratch each time. Nothing outside that directory is touched.

```bash
uv run python examples/setup_demo.py
```

`cleanup_agent.py` is the subject: a plausible tidy-up script of the kind you get
from an agent, or from yourself at the end of a long day. Reading it quickly, it
looks fine. It is not.

## From the command line

```bash
cd examples/demo
consequence plan cleanup_agent.py
```

## From Python

| file | what it shows |
| --- | --- |
| `plan_it.py` | plan mode through the library, and reading the effects back |
| `guard_it.py` | guard mode: real execution inside `safe.toml`, stopped at the first refusal |
| `test_purity.py` | the pytest fixtures, as tests you can run |

```bash
uv run python examples/plan_it.py
uv run python examples/guard_it.py
uv run pytest examples/test_purity.py -p consequence
```

`safe.toml` is a policy worth reading on its own — it is the whole configuration
surface in one file.

## Checking the plan against reality

`fidelity_check.py` is not a demo. It runs a program of your choosing twice, from
identical copies of a seed directory — once under plan mode, once for real — and
answers two questions: did the plan change anything on disk, and did it predict
everything the real run did.

```bash
python examples/fidelity_check.py --seed ./a-copy-of-your-project -- -m mypkg up
```

Point it at a copy. The second run is real.
