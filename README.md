# Alfred 5 StackExchange Search

## Installation

In terminal (replace `/path/to/repo` to your directory path):

```bash
git clone https://github.com/kkew3/alfred-stackoverflow.git /path/to/repo
cd /path/to/repo
# create python virtual environment
python3 -m venv venv
# activate the environment
. venv/bin/activate
# install dependencies
pip install -r requirements.txt
# install this package
pip install .
# let's call the python3 path in clipboard "Python Runtime"
which python3 | tr -d '\n' | pbcopy
# deactivate the environment
deactivate
```

Double-click `StackExchange Search.alfredworkflow`.
When configuring the workflow, paste the Python Runtime into field `Python Runtime`.
Then configure other items to your need.

## Usage

### Upon installation

Upon installation, invoke `stack-cache-sites` keyword to collect sites info.
This make takes several minutes.
Notification will be posted on complete.

### Afterwards

There are two options to query the workflow.

1. Invoke `stack` script filter, select one site from list, then input the query for that site;
2. Create a script filter yourself that connects to the `Open URL` block, and invoke that script filter.
   An illustrative shortcut script filter has already been created, named `so` for searching Stack Overflow.

Detailed usage:

- `stack` script filter:
    - query format: the site name in which to search
    - `command + enter`: reveal the site icon in Finder
    - `command + c`: copy the `site_id` (useful when creating shortcut script filters)
    - `command + l`: show the `site_id` in large type
- any script filter connected to the `Open URL` block:
    - query format: `query words [#tag1 #tag2] [// local search words]`, where `query words` and `#tag1 #tag2` are used to request the StackExchange server, and `// local search words` is used to search in local cache (thus cheaper)
    - `command + c`: copy the question title
    - `command + l`: show the question title in large type

## Python dependencies

- `python >= 3.7`
- [`Pillow`](https://pillow.readthedocs.io/en/stable/)
- [`requests`](https://requests.readthedocs.io/en/latest/)

## Other dependencies

- [`fzf`](https://github.com/junegunn/fzf)

## Acknowledgement

This project is heavily inspired by

- [`StackExchange Search for Alfred`](https://github.com/deanishe/alfred-stackexchange)

Difference:

- Alfred 5 and modern python3 support
