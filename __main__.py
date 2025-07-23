"""The comand line module of LuaTaint."""
import logging
import os
import sys
import time
import psutil
from collections import defaultdict

from analysis.constraint_table import initialize_constraint_table
from analysis.fixed_point import analyse
from cfg import make_cfg
from core.ast_helper import generate_ast, is_compiled_lua
from core.project_handler import (
    get_directory_modules,
    get_modules
)
from usage import parse_args
from vulnerabilities import (
    find_vulnerabilities,
    get_vulnerabilities_not_in_baseline,
    filter_non_external_inputs
)
from vulnerabilities.vulnerability_helper import SanitisedVulnerability
from web_frameworks import (
    FrameworkAdaptor,
    is_django_view_function,
    is_luci_route_function,
    is_function,
    is_function_without_leading_
)

log = logging.getLogger(__name__)


def log_memory(stage):
    """Helper to log current memory usage."""
    process = psutil.Process(os.getpid())
    mem_mb = process.memory_info().rss / (1024 * 1024)
    log.debug("Memory usage %s: %.1f MB", stage, mem_mb)

def discover_files(targets, excluded_files, recursive=False):
    included_files = list()
    excluded_list = [f for f in excluded_files.split(",") if f]
    for target in targets:
        if os.path.isdir(target):
            for root, _, files in os.walk(target):
                for file in files:
                    fullpath = os.path.join(root, file)
                    if file.endswith('.lua') and fullpath not in excluded_list:
                        if not is_compiled_lua(fullpath):
                            included_files.append(fullpath)
                            log.debug('Discovered file: %s', fullpath)
                        else:
                            log.warning('Skipping compiled Lua file: %s', fullpath)
                if not recursive:
                    break
        else:
            if target.endswith('.lua') and target not in excluded_list:
                if not is_compiled_lua(target):
                    included_files.append(target)
                    log.debug('Discovered file: %s', target)
                else:
                    log.warning('Skipping compiled Lua file: %s', target)
            else:
                log.warning('Skipping non-Lua file: %s', target)
    return included_files

def retrieve_nosec_lines(
    path
):
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as file:
            lines = file.readlines()
    except OSError as exc:
        log.error('Failed to read %s: %s', path, exc)
        return set()
    return set(
        lineno for
        (lineno, line) in enumerate(lines, start=1)
        if '#nosec' in line or '# nosec' in line
    )

def main(command_line_args=sys.argv[1:]):  # noqa: C901
    args = parse_args(command_line_args)
    logging_level = (
        logging.ERROR if not args.verbose else
        logging.WARN if args.verbose == 1 else
        logging.INFO if args.verbose == 2 else
        logging.DEBUG
    )
    logging.basicConfig(level=logging_level, format='[%(levelname)s] %(name)s: %(message)s')

    files = discover_files(
        args.targets,
        args.excluded_paths,
        True
    )
    log_memory("after discovering files")

    nosec_lines = defaultdict(set)

    if args.project_root:
        directory = os.path.normpath(args.project_root)
        project_modules = get_modules(directory, prepend_module_root=args.prepend_module_root)
    
    cfg_list = list()
    for path in sorted(files):
        #cfg_list = list()
        log.info("Processing %s", path)
        if not args.ignore_nosec:
            nosec_lines[path] = retrieve_nosec_lines(path)

        if not args.project_root:
            directory = os.path.dirname(path)
            project_modules = get_modules(directory, prepend_module_root=args.prepend_module_root)

        local_modules = get_directory_modules(directory)
        tree = generate_ast(path)
        log_memory(f"after AST for {os.path.basename(path)}")

        cfg = make_cfg(
            tree,
            project_modules,
            local_modules,
            path,
            allow_local_directory_imports=args.allow_local_imports
        )
        log_memory(f"after CFG for {os.path.basename(path)}")
        cfg_list = [cfg]
        
        framework_entry_criteria = is_luci_route_function

        # Add all the route functions to the cfg_list
        FrameworkAdaptor(
            cfg_list,
            project_modules,
            local_modules,
            framework_entry_criteria
        )
    
    initialize_constraint_table(cfg_list)
    log_memory("after initialize_constraint_table")
    log.info("Analysing")
    analyse(cfg_list)
    log_memory("after analysis")
    log.info("Finding vulnerabilities")
    
    #vulnerabilities = list()
    vulnerabilities = find_vulnerabilities(
        cfg_list,
        args.blackbox_mapping_file,
        args.trigger_word_file,
        args.interactive,
        nosec_lines
    )
    log_memory("after find_vulnerabilities")

    filter_non_external_inputs(vulnerabilities)
    log_memory("after filter_non_external_inputs")

    if args.baseline:
        vulnerabilities = get_vulnerabilities_not_in_baseline(
            vulnerabilities,
            args.baseline
        )
        log_memory("after baseline comparison")
        
    args.formatter.report(vulnerabilities, args.output_file, args.only_unsanitised)
    log_memory("after report generation")
    
    '''has_unsanitised_vulnerabilities = any(
        not isinstance(v, SanitisedVulnerability)
        for v in vulnerabilities
    )
    
    if has_unsanitised_vulnerabilities:
        sys.exit(1)'''


if __name__ == '__main__':
    main()
