"""PipelineBench: does context make agents safer on data pipelines?

Fifty scenarios over the fixture platform, each a proposed change with
ground truth written from the platform's own rules (its constraints,
postmortems and lineage), four context conditions, pluggable agents and
mechanical graders. Nothing in the ground truth is produced by Fathom; the
``check-ground-truth`` command compares Fathom to it as one more subject.
"""

__version__ = "0.1.0"
