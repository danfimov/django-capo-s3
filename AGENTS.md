# Development Guidelines

`django-capo-s3` is a Django file storage backend for S3-compatible stores, built on the
[capo-s3](https://pypi.org/project/capo-s3/) client instead of boto3. It is a drop-in alternative to
`django-storages[s3]`, so behaviour that looks wrong next to django-storages is often a deliberate difference -
check `docs/django_storages_comparison.md` before "fixing" it.

## Development Workflow

- Run `make lint` (ruff + `mypy src`) and `make test` before reporting a change as done. Both must be clean.
- Tests talk to a real MinIO on `localhost:9000`; start it with `make run_infra`. Without it the whole suite skips,
  so a green run that exercised nothing is not a passing run — check the skip count.
- Do not widen the ruff or mypy configuration to make a change pass. Fix the code, or add a targeted `noqa` on the
  single offending line with a comment saying why the rule does not apply.

## Code Style Guidelines

- Write docstrings that say why the thing exists and what a caller gains from it, not what the code does. A docstring
  that paraphrases the signature is noise.
- Refer to parameters in plain words ("the location prefix", "the file name"), never as `` ``name`` `` or with RST
  roles like `:class:` / `:meth:`.
- Wrap docstrings and comments to the full 120-character line width, not to ~80.

  ```python
  # === BAD - ambiguous function name, restates the signature, quotes argument names RST-style, wraps early ===
  def key(location: str, name: str) -> str:
      """Join ``location`` with ``name`` and return
      the result."""

  # === GOOD - names the things in plain words and explains what the caller gets ===
  def normalize_key(location: str, name: str) -> str:
      """Join the location prefix and a file name into an S3 object key.

      Leading slashes and backslashes are stripped, and a parent-directory segment is rejected so a name cannot
      escape the configured location.
      """
  ```

- The same rule holds for inline comments: they carry the non-obvious reason, never a translation of the line below.

  ```python
  # === BAD - echoes the code ===
  # compress the content with gzip
  body = ContentFile(gzip.compress(content.read(), mtime=0))

  # === GOOD - explains the argument that would otherwise look arbitrary ===
  # mtime=0 keeps the compressed bytes reproducible run-to-run, so skip_unchanged can still match by ETag.
  body = ContentFile(gzip.compress(content.read(), mtime=0))
  ```

- Do not extract a function or a module-level constant that has exactly one call site. Inline it and name the value
  locally instead — an extra name to jump to buys nothing when there is only one place to jump from.

  ```python
  # === BAD - a module constant and a helper used in one place each ===
  _GZIP_WINDOW_BITS = zlib.MAX_WBITS | 16

  def _make_decompressor() -> "zlib._Decompress":
      return zlib.decompressobj(_GZIP_WINDOW_BITS)

  def download_into(self, name: str, target: IO[bytes]) -> None:
      decompressor = _make_decompressor()
      ...

  # === GOOD - inlined at the single call site, with the magic number named where it is used ===
  def download_into(self, name: str, target: IO[bytes]) -> None:
      gzip_window_bits = zlib.MAX_WBITS | 16
      decompressor = zlib.decompressobj(gzip_window_bits)
      ...
  ```

  Extract once there are two or more call sites, or when the name is part of the public surface (an override seam such
  as `object_name()`, or an entry in `__all__`).
- Do not use `from __future__ import annotations` — it is banned in the ruff config. Reach for a string annotation
  only where a genuine import cycle or a `TYPE_CHECKING`-only import demands it.
- Mark every override with `@override` from `typing_extensions`; `explicit-override` is enabled, and Python 3.11 is the
  supported floor, so `typing_extensions` also supplies `Sentinel` and `Unpack`.
- New storage options go into the `S3StorageOptions` TypedDict with a default in `DEFAULTS`, so settings stay a plain
  type-checked dict. Options that would otherwise blow up on the first request are validated in `__init__` and raise
  `ImproperlyConfigured`.
- Anything meant to be public must be re-exported from `src/django_capo_s3/__init__.py` and listed in its `__all__`;
  that module also holds `__version__`, which hatch reads at build time.

## Testing Guidelines

- Name a test for the behaviour it pins down, in the form
  `test_when_<situation>_then_<expected behaviour>`. A name that reads as a sentence makes a failure legible from the
  report alone.

  ```python
  # === BAD - names the method under test, not the behaviour, and needs a docstring to explain itself ===
  def test_save_overwrite(storage: S3Storage) -> None:
      """Check that saving twice under the same name replaces the first object."""

  # === GOOD - the name is the specification, so no docstring is needed ===
  def test_when_the_same_name_is_saved_twice_then_the_stored_object_is_overwritten(storage: S3Storage) -> None: ...
  ```

- Do not write docstrings in tests. If a test needs one to be understood, the name is wrong.
- When tests differ only in their inputs, write one `@pytest.mark.parametrize` with a `pytest.param(..., id="...")` per
  case instead of a family of sibling functions. A differing expectation is a parameter too — pass the expected value
  (or `None` for "nothing happens") alongside the input rather than splitting positive and negative cases apart. Before
  adding a test, check whether an existing parametrize already has its shape and extend that list. Ids are kebab-case
  and show up in the failure output, so make them descriptive.
- Keep functions separate only when the body genuinely differs, not merely the data.
- Test against real MinIO rather than mocks. Build storages through the `storage_factory` / `static_storage_factory`
  fixtures and pass option overrides as keyword arguments; the `bucket` fixture gives each test its own bucket and
  cleans it up. Mocks are for the paths that cannot be reached otherwise, such as a part upload that must fail.
- Every test gets a 2-second budget from the global `--timeout=2`. A test that legitimately needs longer carries an
  explicit `@pytest.mark.timeout(...)` with a comment saying why; benchmarks use `@pytest.mark.timeout(0)`.
- The streaming paths are guarded by `@pytest.mark.limit_memory` (pytest-memray) in `tests/test_features.py`: uploads
  and downloads must not buffer a whole object. A change to those paths has to keep the limits satisfied rather than
  raise them.
- Use pytest style only — `unittest.TestCase` and `django.test.TestCase` are banned imports.

## Documentation

- User-facing behaviour changes belong in `README.md` and the matching page under `docs/`; the tutorial pages are the
  reference the README links into, not a duplicate of it.
- When a change closes a gap that django-storages still has, record it in `docs/django_storages_comparison.md` —
  that page is the argument for this package existing.
