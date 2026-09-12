# Cache Fix

Changes made:
- `main.py`: `load_cache()` now handles missing, empty, invalid, and non-dict JSON safely.
- `main.py`: `save_cache()` writes atomically through a temporary file.
- GitHub Actions: empty cache is initialized as `{}` instead of using `touch`.
- GitHub Actions: cache restore/save uses `actions/cache/restore@v4` and `actions/cache/save@v4` with a per-run key and `restore-keys`.

This prevents `JSONDecodeError: Expecting value: line 1 column 1 (char 0)` from an empty `products_cache.json`.
