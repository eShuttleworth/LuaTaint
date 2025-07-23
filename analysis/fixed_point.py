"""This module implements the fixed point algorithm."""
from collections import deque

from .constraint_table import constraint_table
from .reaching_definitions_taint import ReachingDefinitionsTaintAnalysis


class FixedPointAnalysis:
    """Run the fix point analysis using a worklist algorithm."""

    def __init__(self, cfg):
        """Fixed point analysis.

        Analysis must be a dataflow analysis containing a 'fixpointmethod'
        method that analyses one CFG."""
        self.analysis = ReachingDefinitionsTaintAnalysis(cfg)
        self.cfg = cfg

    def fixpoint_runner(self):
        """Iteratively update nodes until a fixpoint is reached.

        A deque is used for the worklist to avoid the costly list slicing that
        previously led to excessive memory usage on large graphs.
        """
        worklist = deque(self.cfg.nodes)

        while worklist:
            node = worklist.popleft()
            old_constraint = constraint_table[node]
            self.analysis.fixpointmethod(node)
            new_constraint = constraint_table[node]

            if new_constraint != old_constraint:
                for dep_node in self.analysis.dep(node):
                    worklist.append(dep_node)


def analyse(cfg_list):
    """Analyse a list of control flow graphs with a given analysis type."""
    for cfg in cfg_list:
        analysis = FixedPointAnalysis(cfg)
        analysis.fixpoint_runner()
