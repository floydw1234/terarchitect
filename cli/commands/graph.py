"""graph subcommand: get | set"""

import json
import sys
from cli._api import API, APIError
from cli._config import load_config_file
from cli._output import die, print_json


def register(subparsers) -> None:
    p = subparsers.add_parser("graph", help="Architecture graph operations")
    sub = p.add_subparsers(dest="graph_cmd", metavar="SUBCOMMAND")
    sub.required = True

    # get
    g = sub.add_parser("get", help="Print the current graph as JSON")
    g.add_argument("project_id")

    # set
    s = sub.add_parser("set", help="Replace the graph from a JSON file")
    s.add_argument("project_id")
    s.add_argument("--file", "-f", required=True,
                   metavar="FILE", help='JSON file with {"nodes": [...], "edges": [...]}')

    p.set_defaults(func=_dispatch)


def _dispatch(args, api: API) -> None:
    if args.graph_cmd == "get":
        _cmd_get(args, api)
    elif args.graph_cmd == "set":
        _cmd_set(args, api)


# ---------------------------------------------------------------------------

def _cmd_get(args, api: API) -> None:
    try:
        graph = api.get(f"/api/projects/{args.project_id}/graph")
    except APIError as e:
        die(str(e))
    # Always JSON output — graph data is not table-friendly
    print_json(graph)


def _cmd_set(args, api: API) -> None:
    data = load_config_file(args.file)
    if not isinstance(data, dict) or "nodes" not in data or "edges" not in data:
        die('File must be a JSON object with "nodes" and "edges" keys')
    try:
        result = api.put(f"/api/projects/{args.project_id}/graph", {
            "nodes": data["nodes"],
            "edges": data["edges"],
        })
    except APIError as e:
        die(str(e))
    version = (result or {}).get("version", "?")
    print(f"Graph updated (version {version}). "
          f"{len(data['nodes'])} nodes, {len(data['edges'])} edges.")
