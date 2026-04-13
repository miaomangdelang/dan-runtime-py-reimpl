# Reverse Engineering Tools (Python)

These scripts avoid external dependencies and can run offline. They are intended to extract structure from Go binaries.

## Scripts
- `extract_strings.py`  
  Extracts and filters strings for URLs, API routes, and config keys.
- `callgraph.py`  
  Builds a callgraph edge list from `go tool objdump` output.
- `buildinfo.py`  
  Reads Go build info using `go version -m` if available.

## Example Usage
```bash
python3 tools/reverse/buildinfo.py dan-linux-amd64
python3 tools/reverse/extract_strings.py dan-linux-amd64 --mode urls
go tool objdump -s 'dan/internal/danapp.(*RegisterSession).runRegister' dan-linux-amd64 > /tmp/objdump.txt
python3 tools/reverse/callgraph.py /tmp/objdump.txt
```

Notes:
- Dynamic or network execution is intentionally excluded.
- For deeper analysis, use `go tool nm` and `go tool objdump` alongside these scripts.

