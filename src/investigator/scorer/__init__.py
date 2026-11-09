"""The scorer: the only component that reads the answer key (as scorer_ro).

truth.py    the answer key of one incident, as a TruthView
metrics.py  score one FinalReport against a TruthView (pure; golden-tested)
store.py    score a finished run from eval.* and write eval.scores (as harness_rw)
"""
