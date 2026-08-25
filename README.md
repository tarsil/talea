# Talea

Talea is an early-stage Python 3.14 data-modelling library. Its current
implementation resolves a deliberately small set of Python annotations into
immutable structural schema values. Runtime validation, models, parsing, and
serialization have not been implemented yet.

The canonical schema foundation currently covers built-in scalar types,
homogeneous built-in containers, dictionaries, fixed and variadic tuples, and
PEP 604 unions composed from those forms. This foundation remains internal and
is not exported from the root `talea` package.
