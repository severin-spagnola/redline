# Demo: quant backtest with a redlined signal clause

A minimal example showing how Redline protects the causal-signal invariant of a
backtest. `strat/redline.meta.json` marks the strategy `conditional`; the signal
clause inside `strat/golden.py` is `never` via an intra-file marker.

Try it:

    # from this directory, in a git repo:
    python ../../arch_gate.py --policy arch.policy.json --base HEAD~1 --head HEAD

Edit the `# arch:begin signal-clause never` region (e.g. add `.shift(-1)`) and the
gate blocks the merge with a prescriptive message. Delete the markers and it still
blocks (guard-deletion).
