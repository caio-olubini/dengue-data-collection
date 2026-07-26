"""Post-collection transformation pipelines.

`src.collection` downloads raw data; this package turns it into the analysis
tables the data paper describes. One subpackage per source, so each can evolve
independently — `sinan` is the only one implemented so far.
"""
