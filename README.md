# unpaywall-downloader

Download open-access PDFs for one or more DOIs using the Unpaywall API.

## Setup

Install the project environment:

```bash
uv sync
```

## Install For One Account

To install the CLI for your own user account so you can run `unpaywall`
directly from anywhere:

```bash
uv tool install .
uv tool update-shell
```

This is the recommended installation method for normal usage.

After opening a new shell, the command is available as:

```bash
unpaywall --doi 10.1038/s41586-021-03819-2
```

To remove it later:

```bash
uv tool uninstall unpaywall-downloader
```

## Install System-Wide

To install the CLI into the system Python environment for all users on the
machine:

```bash
sudo uv pip install --system .
```

After that, the command should be available as:

```bash
unpaywall --doi 10.1038/s41586-021-03819-2
```

If your system Python is marked as externally managed, you may also need:

```bash
sudo uv pip install --system --break-system-packages .
```

Unpaywall requires an email address. You can pass it on the command line with
`--email`, or set it once in your shell:

```bash
export UNPAYWALL_EMAIL=you@example.com
```

## Usage

Run the CLI through `uv`:

```bash
uv run unpaywall --doi <doi>
```

## Examples

Single famous paper:

```bash
uv run unpaywall --doi 10.1038/s41586-021-03819-2
```

Batch of all four, with files auto-named in the current directory:

```bash
uv run unpaywall \
  --doi 10.1038/s41586-021-03819-2 \
  --doi 10.1038/s41586-024-07487-w \
  --doi 10.1126/science.1225829 \
  --doi 10.1056/NEJMoa2034577
```

Pass the email on the command line:

```bash
uv run unpaywall \
  --doi 10.1038/s41586-021-03819-2 \
  --email you@example.com
```

Set the email once, then run a batch without repeating `--email`:

```bash
export UNPAYWALL_EMAIL=you@example.com
uv run unpaywall \
  --doi 10.1038/s41586-021-03819-2 \
  --doi 10.1126/science.1225829
```

Batch into a folder, using the email from the environment:

```bash
export UNPAYWALL_EMAIL=you@example.com
uv run unpaywall \
  --doi 10.1038/s41586-021-03819-2 \
  --doi 10.1038/s41586-024-07487-w \
  --output ./pdfs/
```
