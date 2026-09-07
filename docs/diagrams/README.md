# Simulation class diagram

Open [the SVG diagram](classes_rideshare.svg) to zoom in, or use
[the PNG diagram](classes_rideshare.png). The [DOT source](classes_rideshare.dot)
is the unmodified output of Pyreverse, included with Pylint 3.3.9.

The diagram covers all nine classes in `main.py`, including constructors,
attributes, public methods, and private helpers. Tests and archived code are
outside its scope. Each class box lists its name, attributes, then methods.

Pyreverse analyzes source code without running the simulation. With this
version and the current source, it infers only three relationships: a driver's
session, a rider's session, and an offer's timeout event. Other relationships
exist at runtime but are missing from this diagram. For example, an order
refers to its rider session and, after acceptance, its driver session.

Types are also inferred and can be incomplete. `current_order : NoneType`
reflects initialization to `None`; it does not mean the attribute must stay
`None`. Similarly, an inferred `int` from a default value is not an enforced
restriction on that attribute. Use the diagram as a class reference alongside
the source, rather than as a complete description of runtime behavior.

To recreate the local environment, run these commands from the repository root
(Graphviz must also be installed and available on `PATH`):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install 'pylint==3.3.9'
```

To regenerate the diagram from the current source:

```sh
mkdir -p docs/diagrams
.venv/bin/pyreverse --filter-mode ALL --module-names n \
  --project rideshare --output dot --output-directory docs/diagrams main.py

ccomps -x docs/diagrams/classes_rideshare.dot \
  | dot -Nfontname=Helvetica -Efontname=Helvetica -Tdot \
  | gvpack -array3 \
  | neato -n2 -Gpad=0.25 -Tsvg -o docs/diagrams/classes_rideshare.svg

ccomps -x docs/diagrams/classes_rideshare.dot \
  | dot -Nfontname=Helvetica -Efontname=Helvetica -Tdot \
  | gvpack -array3 \
  | neato -n2 -Gpad=0.25 -Gdpi=110 -Tpng -o docs/diagrams/classes_rideshare.png
```

The rendering pipeline lays out connected components separately before packing
them into three columns; this preserves edge labels with the installed
Graphviz 15.1.1. `ccomps` returns status 1 when a graph is disconnected, which is
expected here if your shell enables `pipefail`.
