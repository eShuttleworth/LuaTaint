# LuaTaint
An automated static taint analysis tool for the Lua web framework.

## Setup

Install the Python dependencies listed in `requirements.txt` before running the tool. A convenience script is provided:

```bash
./scripts/setup.sh
```

## Usage

Run the tool on one or more Lua files or directories:

```bash
python3 __main__.py <targets> [options]
```

Key options include:

* `-v`, `--verbose` - increase logging verbosity (can be repeated).
* `-a`, `--adaptor` - choose a web framework adaptor (`Flask` by default, `Django`, `Every`, or `Pylons`).
* `-pr`, `--project-root` - specify the project root when the entry file is not at the root.
* `-b`, `--baseline` - path to a baseline JSON file to compare results.
* `-t`, `--trigger-word-file` - file containing sources and sinks definitions.
* `-m`, `--blackbox-mapping-file` - file with mappings for blackbox functions.
* `-i`, `--interactive` - prompt for each blackbox function call in vulnerability chains.
* `-o`, `--output` - write the report to a file (defaults to stdout).
* `--ignore-nosec` - do not skip lines marked with `# nosec`.
* `-r`, `--recursive` - search for Lua files in subdirectories.
* `-x`, `--exclude` - comma separated list of files to exclude.
* `--dont-prepend-root` - do not prepend the project root to imports.
* `--no-local-imports` - require absolute imports relative to the project root.
* `-u`, `--only-unsanitised` - hide sanitised vulnerabilities.
* `-j`, `--json` - output the results as JSON.
* `-s`, `--screen` - display a colourful report on the terminal.

## Running Tests

After installing the dependencies, execute the test suite with:

```bash
./scripts/run_tests.sh
```

