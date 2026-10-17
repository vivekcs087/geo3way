# Contributing

Thanks for helping. Issues and pull requests are welcome.

* **Bug reports:** include the geo3way version (`geo3way --version`), the command you ran,
  and if possible three small files (base, ours, theirs) that reproduce the problem.
* **Code:** keep the library dependency-free (standard library only). Add a test for every
  fix or feature. Run before opening a pull request:

  ```bash
  pytest
  ruff check src tests
  mypy src
  ```

* **Design changes** (new conflict kinds, file formats, changed defaults): please open an
  issue first, so we can agree on the behaviour before you write the code.

Layout: `src/geo3way/` (library: `diff`, `merge`, `geometry`, `io`, `cli`), `tests/`,
`examples/` (sample data), `experiments/` and `results/` (the paper's evaluation), `docs/`.
