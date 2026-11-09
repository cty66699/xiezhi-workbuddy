# Runtime notes

The CLI default `--root` is the current working directory's `runs/` folder. Earlier design drafts may refer to `xiezhi-runs/`; those drafts are retained only as historical design sources and are frozen for evidence integrity.

For a historical run, do not modify its source files. Put later documentation changes in separate files, otherwise `verify` will correctly report a source hash mismatch.
