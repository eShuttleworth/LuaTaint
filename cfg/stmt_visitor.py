from lua_parser import ast
import itertools
import logging
import os.path
from pkgutil import iter_modules

from .alias_helper import (
    as_alias_handler,
    fully_qualify_alias_labels,
    handle_aliases_in_init_files,
    handle_fdid_aliases,
    not_as_alias_handler,
    retrieve_import_alias_mapping
)
from core.ast_helper import (
    generate_ast,
    get_call_names,
    get_call_names_as_string
)
from core.module_definitions import (
    LocalModuleDefinition,
    ModuleDefinition,
    ModuleDefinitions
)
from core.node_types import (
    AssignmentNode,
    AssignmentCallNode,
    BBorBInode,
    BreakNode,
    ControlFlowNode,
    EntryOrExitNode,
    IfNode,
    ElseIfNode,
    IgnoredNode,
    Node,
    #RaiseNode,
    ReturnNode
)
from core.project_handler import (
    get_directory_modules
)
from helper_visitors import (
    LabelVisitor,
    RHSVisitor,
    VarsVisitor
)
from .stmt_visitor_helper import (
    CALL_IDENTIFIER,
    ConnectStatements,
    connect_nodes,
    extract_left_hand_side,
    get_first_node,
    get_first_statement,
    get_last_statements,
    remove_breaks
)

log = logging.getLogger(__name__)
uninspectable_modules = {module.name for module in iter_modules()}  # Don't warn about failing to import these
ast_Compare = (
    ast.EqToOp,
    ast.GreaterThanOp,
    ast.LessThanOp,
    ast.NotEqToOp,
    ast.GreaterOrEqThanOp,
    ast.LessOrEqThanOp
    )

class StmtVisitor(ast.NodeVisitor):
    def __init__(self, allow_local_directory_imports=True):
        self._allow_local_modules = allow_local_directory_imports
        super().__init__()
    
    '''def visit_SemiColon(self, node):
        return'''
    
    def visit_Block(self, node):
        return self.stmt_star_handler(node.body)

    def stmt_star_handler(
        self,
        stmts,
        prev_node_to_avoid=None
    ):
        """Handle stmt* expressions in an AST node.

        Links all statements together in a list of statements, accounting for statements with multiple last nodes.
        """
        break_nodes = list()
        cfg_statements = list()

        self.prev_nodes_to_avoid.append(prev_node_to_avoid)
        self.last_control_flow_nodes.append(None)

        first_node = None
        node_not_to_step_past = self.nodes[-1]

        for stmt in stmts:
            if isinstance(stmt, ast.SemiColon):
                continue
            elif isinstance(stmt, ast.Do):
                for n in stmt.body.body:
                    node = self.visit(n)
            else:
                node = self.visit(stmt)
            
            #if isinstance(node, ControlFlowNode) and not isinstance(node.test, TryNode):
            if isinstance(node, ControlFlowNode):
                self.last_control_flow_nodes.append(node.test)
            else:
                self.last_control_flow_nodes.append(None)

            if isinstance(node, ControlFlowNode):
                break_nodes.extend(node.break_statements)
            elif isinstance(node, BreakNode):
                break_nodes.append(node)

            if not isinstance(node, IgnoredNode):
                cfg_statements.append(node)
                if not first_node:
                    if isinstance(node, ControlFlowNode):
                        first_node = node.test
                    else:
                        first_node = get_first_node(
                            node,
                            node_not_to_step_past
                        )

        self.prev_nodes_to_avoid.pop()
        self.last_control_flow_nodes.pop()

        connect_nodes(cfg_statements)

        if cfg_statements:
            if first_node:
                first_statement = first_node
            else:
                first_statement = get_first_statement(cfg_statements[0])

            last_statements = get_last_statements(cfg_statements)

            return ConnectStatements(
                first_statement=first_statement,
                last_statements=last_statements,
                break_statements=break_nodes
            )
        else:  # When body of module only contains ignored nodes
            return IgnoredNode()
    
    def get_parent_definitions(self):
        parent_definitions = None
        if len(self.module_definitions_stack) > 1:
            parent_definitions = self.module_definitions_stack[-2]
        return parent_definitions

    def add_to_definitions(self, node):
        local_definitions = self.module_definitions_stack[-1]
        parent_definitions = self.get_parent_definitions()

        if parent_definitions:
            parent_qualified_name = '.'.join(
                parent_definitions.classes +
                [node.name]
            )
            parent_definition = ModuleDefinition(
                parent_definitions,
                parent_qualified_name,
                local_definitions.module_name,
                self.filenames[-1]
            )
            parent_definition.node = node
            parent_definitions.append_if_local_or_in_imports(parent_definition)
        if isinstance(node.name,ast.Index):
            local_qualified_name = '.'.join(local_definitions.classes +
                                        [node.name.idx.id])
        else:
            local_qualified_name = '.'.join(local_definitions.classes +
                                        [node.name.id])
        local_definition = LocalModuleDefinition(
            local_definitions,
            local_qualified_name,
            None,
            self.filenames[-1]
        )
        local_definition.node = node
        local_definitions.append_if_local_or_in_imports(local_definition)
        if isinstance(node.name,ast.Index):
            self.function_names.append(node.name.idx.id)
        else:
            self.function_names.append(node.name.id)
    def visit_Function(self, node):
        self.add_to_definitions(node)

        return IgnoredNode()

    def visit_LocalFunction(self, node):
        self.add_to_definitions(node)

        return IgnoredNode()

    def visit_Method(self, node):
        return IgnoredNode()

    def handle_or_else(self, orelse, test):
        """Handle the orelse part of an if node.

        Args:
            orelse(list[Node])
            test(Node)

        Returns:
            The last nodes of the orelse branch.
        """
        if isinstance(orelse, ast.ElseIf):
            control_flow_node = self.visit(orelse)
            # Prefix the if label with 'el'
            control_flow_node.test.label = control_flow_node.test.label

            test.connect(control_flow_node.test)
            return control_flow_node.last_nodes
        else:
            else_connect_statements = self.stmt_star_handler(
                orelse.body,
                prev_node_to_avoid=self.nodes[-1]
            )
            test.connect(else_connect_statements.first_statement)
            return else_connect_statements.last_statements

    def visit_If(self, node):
        test = self.append_node(IfNode(
            node.test,
            node,
            path=self.filenames[-1]
        ))

        body_connect_stmts = self.stmt_star_handler(node.body.body)
        if isinstance(body_connect_stmts, IgnoredNode):
            body_connect_stmts = ConnectStatements(
                first_statement=test,
                last_statements=[],
                break_statements=[]
            )
        test.connect(body_connect_stmts.first_statement)

        if node.orelse:
            if node.orelse.body != []:
                orelse_last_nodes = self.handle_or_else(node.orelse, test)
                body_connect_stmts.last_statements.extend(orelse_last_nodes)
            else:
                body_connect_stmts.last_statements.append(test)  # if there is no orelse, test needs an edge to the next_node
        else:
            body_connect_stmts.last_statements.append(test)  # if there is no orelse, test needs an edge to the next_node

        last_statements = remove_breaks(body_connect_stmts.last_statements)

        return ControlFlowNode(test, last_statements, break_statements=body_connect_stmts.break_statements)

    def visit_ElseIf(self, node):
        test = self.append_node(ElseIfNode(
            node.test,
            node,
            path=self.filenames[-1]
        ))

        body_connect_stmts = self.stmt_star_handler(node.body.body)
        if isinstance(body_connect_stmts, IgnoredNode):
            body_connect_stmts = ConnectStatements(
                first_statement=test,
                last_statements=[],
                break_statements=[]
            )
        test.connect(body_connect_stmts.first_statement)

        if node.orelse:
            if node.orelse.body != []:
                orelse_last_nodes = self.handle_or_else(node.orelse, test)
                body_connect_stmts.last_statements.extend(orelse_last_nodes)
            else:
                body_connect_stmts.last_statements.append(test)
        else:
            body_connect_stmts.last_statements.append(test)  # if there is no orelse, test needs an edge to the next_node

        last_statements = remove_breaks(body_connect_stmts.last_statements)

        return ControlFlowNode(test, last_statements, break_statements=body_connect_stmts.break_statements)

    def get_id_from_ast(self, node):
        if not isinstance(node, ast.Index):
            return node.id
        else: 
            return self.get_id_from_ast(node.value) + node.idx.id

    def visit_Return(self, node):
        label = LabelVisitor()
        label.visit(node)

        if self.function_return_stack:
            this_function_name = self.function_return_stack[-1]
            if not isinstance(this_function_name, str):
                this_function_name = self.get_id_from_ast(this_function_name)
        else:
            this_function_name = os.path.basename(self.filenames[-1]).split('.')[0]

        LHS = 'ret_' + this_function_name + '(cbi_return)'

        if node.values and isinstance(node.values[0], (ast.Call, ast.Invoke)):
            return_value_of_call = self.visit(node.values[0])
            return_node = ReturnNode(
                LHS + ' = ' + return_value_of_call.left_hand_side,
                LHS,
                node,
                [return_value_of_call.left_hand_side],
                path=self.filenames[-1]
            )
            return_value_of_call.connect(return_node)
            return self.append_node(return_node)
        elif node.values and len(node.values)>0:
            rhs_visitor_result = RHSVisitor.result_for_node(node.values[0])
        else:
            rhs_visitor_result = []

        return self.append_node(ReturnNode(
            LHS + ' = ' + label.result,
            LHS,
            node,
            rhs_visitor_result,
            path=self.filenames[-1]
        ))

    def handle_stmt_star_ignore_node(self, body, fallback_cfg_node):
        try:
            fallback_cfg_node.connect(body.first_statement)
        except AttributeError:
            body = ConnectStatements(
                first_statement=[fallback_cfg_node],
                last_statements=[fallback_cfg_node],
                break_statements=[]
            )
        return body

    def assign_tuple_target(self, target_nodes, value_nodes, right_hand_side_variables):
        new_assignment_nodes = []
        remaining_variables = list(right_hand_side_variables)
        remaining_targets = list(target_nodes)
        remaining_values = list(value_nodes)  # May contain duplicates

        def visit(target, value):
            label = LabelVisitor()
            label.visit(target)
            rhs_visitor = RHSVisitor()
            rhs_visitor.visit(value)
            if isinstance(value, ast.Call):
                new_ast_node = ast.Assign(target, value)
                #ast.copy_location(new_ast_node, target)
                new_assignment_nodes.append(self.assignment_call_node(label.result, new_ast_node))
            else:
                label.result += ' = '
                label.visit(value)
                new_assignment_nodes.append(self.append_node(AssignmentNode(
                    label.result,
                    extract_left_hand_side(target),
                    ast.Assign(target, value),
                    rhs_visitor.result,
                    line_number=target.line,
                    path=self.filenames[-1]
                )))
            remaining_targets.remove(target)
            remaining_values.remove(value)
            for var in rhs_visitor.result:
                remaining_variables.remove(var)

        # Pair targets and values until a Starred node is reached
        for target, value in zip(target_nodes, value_nodes):
            visit(target, value)

        # If there was a Starred node, pair remaining targets and values from the end
        for target, value in zip(reversed(list(remaining_targets)), reversed(list(remaining_values))):
            visit(target, value)

        if remaining_targets:
            label = LabelVisitor()
            label.handle_comma_separated(remaining_targets)
            label.result += ' = '
            label.handle_comma_separated(remaining_values)
            for target in remaining_targets:
                new_assignment_nodes.append(self.append_node(AssignmentNode(
                    label.result,
                    extract_left_hand_side(target),
                    ast.Assign(target, remaining_values[0]),
                    remaining_variables,
                    line_number=target.line,
                    path=self.filenames[-1]
                )))

        connect_nodes(new_assignment_nodes)
        return ControlFlowNode(new_assignment_nodes[0], [new_assignment_nodes[-1]], [])  # return the last added node
    
    '''elif len(node.targets) > 1:                # x = y = 3
            return self.assign_multi_target(node, rhs_visitor.result)'''
    '''def assign_multi_target(self, node, right_hand_side_variables):
        new_assignment_nodes = list()

        for target in node.targets:
            label = LabelVisitor()
            label.visit(target)
            left_hand_side = label.result
            label.result += ' = '
            label.visit(node.value)
            new_assignment_nodes.append(self.append_node(AssignmentNode(
                label.result,
                left_hand_side,
                ast.Assign(target, node.value),
                right_hand_side_variables,
                line_number=node.lineno,
                path=self.filenames[-1]
            )))

        connect_nodes(new_assignment_nodes)
        return ControlFlowNode(new_assignment_nodes[0], [new_assignment_nodes[-1]], [])  # return the last added node
    '''

    def visit_Assign(self, node):
        rhs_visitor = RHSVisitor()
        for node_values in node.values:
            rhs_visitor.visit(node_values)
        if len(node.targets) > 1:  # x,y = [1,2]
            if len(node.values) > 1:
                return self.assign_tuple_target(node.targets, node.values, rhs_visitor.result)
            elif len(node.values) > 0 and isinstance(node.values[0], ast.Call):
                call = None
                for element in node.targets:
                    label = LabelVisitor()
                    label.visit(element)
                    call = self.assignment_call_node(label.result, node)
                return call
            else:
                label = LabelVisitor()
                label.visit(node)
                log.warning(
                    'Assignment not properly handled at %s:%s. Could result in not finding a vulnerability. Assignment: %s',
                    getattr(self, 'filenames', ['?'])[0],
                    getattr(node, 'line', '?'),
                    label.result,
                )
                return self.append_node(AssignmentNode(
                    label.result,
                    label.result,
                    node,
                    rhs_visitor.result,
                    path=self.filenames[-1]
                ))
        else:
            if len(node.values) > 0 and isinstance(node.values[0], (ast.Call, ast.Invoke)):   # x = call()
                label = LabelVisitor()
                label.visit(node.targets[0])
                return self.assignment_call_node(label.result, node)
            else:                                  # x = 4
                label = LabelVisitor()
                label.visit(node)
                return self.append_node(AssignmentNode(
                    label.result,
                    extract_left_hand_side(node.targets[0]),
                    node,
                    rhs_visitor.result,
                    path=self.filenames[-1]
                ))

    def visit_LocalAssign(self, node):
        rhs_visitor = RHSVisitor()
        for node_values in node.values:
            rhs_visitor.visit(node_values)
        if len(node.targets) > 1:  # x,y = [1,2]
            if len(node.values) > 1:
                return self.assign_tuple_target(node.targets, node.values, rhs_visitor.result)
            elif len(node.values) > 0 and isinstance(node.values[0], ast.Call):
                call = None
                for element in node.targets:
                    label = LabelVisitor()
                    label.visit(element)
                    call = self.assignment_call_node(label.result, node)
                return call
            else:
                label = LabelVisitor()
                label.visit(node)
                log.warning(
                    'Assignment not properly handled at %s:%s. Could result in not finding a vulnerability. Assignment: %s',
                    getattr(self, 'filenames', ['?'])[0],
                    getattr(node, 'line', '?'),
                    label.result,  # self.label.result
                )
                return self.append_node(AssignmentNode(
                    label.result,
                    label.result,
                    node,
                    rhs_visitor.result,
                    path=self.filenames[-1]
                ))
        else:
            if len(node.values) > 0 and isinstance(node.values[0], (ast.Call, ast.Invoke)):   # x = call()
                label = LabelVisitor()
                label.visit(node.targets[0])
                return self.assignment_call_node(label.result, node)
            else:                                  # x = 4
                label = LabelVisitor()
                label.visit(node)
                return self.append_node(AssignmentNode(
                    label.result,
                    extract_left_hand_side(node.targets[0]),
                    node,
                    rhs_visitor.result,
                    path=self.filenames[-1]
                ))
                
    def assignment_call_node(self, left_hand_label, ast_node):
        """Handle assignments that contain a function call on its right side."""
        self.undecided = True  # Used for handling functions in assignments
        
        if isinstance(ast_node.values, list):
            call = self.visit(ast_node.values[0])
        else:
            call = self.visit(ast_node.values)
        call_label = call.left_hand_side

        call_assignment = AssignmentCallNode(
            left_hand_label + ' = ' + call_label,
            left_hand_label,
            ast_node,
            [call.left_hand_side],
            line_number=ast_node.line,
            path=self.filenames[-1],
            call_node=call
        )
        call.connect(call_assignment)

        self.nodes.append(call_assignment)
        self.undecided = False

        return call_assignment

    def loop_node_skeleton(self, test, node):
        """Common handling of looped structures, while and for."""
        body_connect_stmts = self.stmt_star_handler(
            node.body.body,
            prev_node_to_avoid=self.nodes[-1]
        )
        last_nodes = list()
        if not isinstance(body_connect_stmts,IgnoredNode):
            test.connect(body_connect_stmts.first_statement)
            test.connect_predecessors(body_connect_stmts.last_statements)

            # last_nodes is used for making connections to the next node in the parent node
            # this is handled in stmt_star_handler
            last_nodes.extend(body_connect_stmts.break_statements)
        last_nodes.append(test)  # if there is no orelse, test needs an edge to the next_node

        return ControlFlowNode(test, last_nodes, list())

    def visit_Forin(self, node):
        self.undecided = False

        iterator_label = LabelVisitor()
        iterator_label.visit(node.iter[0])
        target_label = LabelVisitor()
        for element in range(len(node.targets)-1):
            target_label.visit(node.targets[element])
            target_label.result += ', '

        target_label.visit(node.targets[-1])

        forin_node = self.append_node(Node(
            "for " + target_label.result + " in " + iterator_label.result + ' do',
            node,
            path=self.filenames[-1]
        ))

        self.process_loop_funcs(node.iter[0], forin_node)

        return self.loop_node_skeleton(forin_node, node)
        
    def visit_Fornum(self, node):
        self.undecided = False

        iterator_label = LabelVisitor()
        iterator_label.visit(node.start)
        iterator_label.result += ','
        iterator_label.visit(node.stop)
        iterator_label.result += ','
        if isinstance(node.step,ast.Number):
            iterator_label.visit(node.step)
        else:
            iterator_label.result += '1'
        target_label = LabelVisitor()
        target_label.visit(node.target)

        fornum_node = self.append_node(Node(
            "for " + target_label.result + " = " + iterator_label.result + ' do',
            node,
            path=self.filenames[-1]
        ))

        return self.loop_node_skeleton(fornum_node, node)
        
    def process_loop_funcs(self, comp_n, loop_node):
        """
        If the loop test node contains function calls, it connects the loop node to the nodes of
        those function calls.

        :param comp_n: The test node of a loop that may contain functions.
        :param loop_node: The loop node itself to connect to the new function nodes if any
        :return: None
        """
        if isinstance(comp_n, ast.Call) and get_call_names_as_string(comp_n.func) in self.function_names:
            last_node = self.visit(comp_n)
            last_node.connect(loop_node)

    def visit_While(self, node):
        label_visitor = LabelVisitor()
        test = node.test  # the test condition of the while loop
        label_visitor.visit(test)

        while_node = self.append_node(Node(
            'while ' + label_visitor.result + ' do',
            node,
            path=self.filenames[-1]
        ))

        if isinstance(test, ast_Compare):
            # quirk. See https://greentreesnakes.readthedocs.io/en/latest/nodes.html#Compare
            self.process_loop_funcs(test.left, while_node)
            self.process_loop_funcs(test.right, while_node)
        else:  # while foo():
            self.process_loop_funcs(test, while_node)

        return self.loop_node_skeleton(while_node, node)

    def visit_Repeat(self, node):
        label_visitor = LabelVisitor()
        test = node.test  # the test condition of the while loop
        label_visitor.visit(test)

        repeat_node = self.append_node(Node(
            'repeat ' + 'until ' + label_visitor.result,
            node,
            path=self.filenames[-1]
        ))

        if isinstance(test, ast_Compare):
            # quirk. See https://greentreesnakes.readthedocs.io/en/latest/nodes.html#Compare
            self.process_loop_funcs(test.left, repeat_node)
            self.process_loop_funcs(test.right, repeat_node)
        else:  # repeat foo():
            self.process_loop_funcs(test, repeat_node)

        return self.loop_node_skeleton(repeat_node, node)
    
    def add_blackbox_or_builtin_call(self, node, blackbox):  # noqa: C901
        """Processes a blackbox or builtin function when it is called.
        Nothing gets assigned to ret_func_foo in the builtin/blackbox case.

        Increments self.function_call_index each time it is called, we can refer to it as N in the comments.
        Create e.g. ~call_1 = ret_func_foo RestoreNode.

        Create e.g. temp_N_def_arg1 = call_arg1_label_visitor.result for each argument.
        Visit the arguments if they're calls. (save_def_args_in_temp)

        I do not think I care about this one actually -- Create e.g. def_arg1 = temp_N_def_arg1 for each argument.
        (create_local_scope_from_def_args)

        Add RestoreNode to the end of the Nodes.

        Args:
            node(ast.Call) : The node that calls the definition.
            blackbox(bool): Whether or not it is a builtin or blackbox call.
        Returns:
            call_node(BBorBInode): The call node.
        """
        self.function_call_index += 1
        saved_function_call_index = self.function_call_index
        self.undecided = False

        call_label_visitor = LabelVisitor()
        call_label_visitor.visit(node)

        call_function_label = call_label_visitor.result[:call_label_visitor.result.find('(')]

        # Check if function call matches a blackbox/built-in alias and if so, resolve it
        # This resolves aliases like "from os import system as mysys" as: mysys -> os.system
        local_definitions = self.module_definitions_stack[-1]
        call_function_label = fully_qualify_alias_labels(call_function_label, local_definitions.import_alias_mapping)

        # Create e.g. ~call_1 = ret_func_foo
        LHS = CALL_IDENTIFIER + 'call_' + str(saved_function_call_index)
        RHS = 'ret_' + call_function_label + '('

        call_node = BBorBInode(
            label='',
            left_hand_side=LHS,
            ast_node=node,
            right_hand_side_variables=[],
            line_number=node.line,
            path=self.filenames[-1],
            func_name=call_function_label
        )
        visual_args = list()
        rhs_vars = list()
        last_return_value_of_nested_call = None

        for arg_node in itertools.chain(node.args):
            #arg = arg_node.value if isinstance(arg_node, ast.keyword) else arg_node
            arg = arg_node
            if isinstance(arg, ast.Call):
                return_value_of_nested_call = self.visit(arg)

                if last_return_value_of_nested_call:
                    # connect inner to other_inner in e.g.
                    # `scrypt.outer(scrypt.inner(image_name), scrypt.other_inner(image_name))`
                    # I should probably loop to the inner most call of other_inner here.
                    try:
                        last_return_value_of_nested_call.connect(return_value_of_nested_call.first_node)
                    except AttributeError:
                        last_return_value_of_nested_call.connect(return_value_of_nested_call)
                else:
                    # I should only set this once per loop, inner in e.g.
                    # `scrypt.outer(scrypt.inner(image_name), scrypt.other_inner(image_name))`
                    # (inner_most_call is used when predecessor is a ControlFlowNode in connect_control_flow_node)
                    call_node.inner_most_call = return_value_of_nested_call
                last_return_value_of_nested_call = return_value_of_nested_call

                '''if isinstance(arg_node, ast.keyword) and arg_node.arg is not None:
                    visual_args.append(arg_node.arg + '=' + return_value_of_nested_call.left_hand_side)
                else:'''
                visual_args.append(return_value_of_nested_call.left_hand_side)
                rhs_vars.append(return_value_of_nested_call.left_hand_side)
            else:
                label = LabelVisitor()
                label.visit(arg_node)
                visual_args.append(label.result)

                vv = VarsVisitor()
                vv.visit(arg_node)
                rhs_vars.extend(vv.result)
        if last_return_value_of_nested_call:
            # connect other_inner to outer in e.g.
            # `scrypt.outer(scrypt.inner(image_name), scrypt.other_inner(image_name))`
            last_return_value_of_nested_call.connect(call_node)

        call_names = list(get_call_names(node.func))
        if len(call_names) > 1:
            # taint is a RHS variable (self) of taint.lower()
            rhs_vars.append(call_names[0])
        if len(visual_args) > 0:
            for arg in visual_args:
                RHS = RHS + arg + ", "
            # Replace the last ", " with a )
            RHS = RHS[:len(RHS) - 2] + ')'
        else:
            RHS = RHS + ')'
        call_node.label = LHS + " = " + RHS

        call_node.right_hand_side_variables = rhs_vars
        # Used in get_sink_args
        rhs_visitor = RHSVisitor()
        rhs_visitor.visit(node)
        call_node.args = rhs_visitor.result

        if blackbox:
            self.blackbox_assignments.add(call_node)

        self.connect_if_allowed(self.nodes[-1], call_node)
        self.nodes.append(call_node)

        return call_node
    
    def visit_Break(self, node):
        return self.append_node(BreakNode(
            node,
            path=self.filenames[-1]
        ))

    '''def visit_Continue(self, node):
        return self.visit_miscelleaneous_node(
            node,
            custom_label='continue'
        )

    def visit_Global(self, node):
        return self.visit_miscelleaneous_node(
            node
        )

    def visit_Pass(self, node):
        return self.visit_miscelleaneous_node(
            node,
            custom_label='pass'
        )
    '''

    def visit_miscelleaneous_node(
        self,
        node,
        custom_label=None
    ):
        if custom_label:
            label = custom_label
        else:
            label_visitor = LabelVisitor()
            label_visitor.visit(node)
            label = label_visitor.result

        return self.append_node(Node(
            label,
            node,
            path=self.filenames[-1]
        ))

    def append_node(self, node):
        """Append a node to the CFG and return it."""
        self.nodes.append(node)
        return node

    def add_module(  # noqa: C901
        self,
        module,
        module_or_package_name,
        local_names,
        import_alias_mapping,
        is_init=False,
        from_from=False,
        from_fdid=False
    ):
        """
        Returns:
            The ExitNode that gets attached to the CFG of the class.
        """
        module_path = module[1]

        parent_definitions = self.module_definitions_stack[-1]
        # Here, in `visit_Import` and in `visit_ImportFrom` are the only places the `import_alias_mapping` is updated
        parent_definitions.import_alias_mapping.update(import_alias_mapping)
        parent_definitions.import_names = local_names

        new_module_definitions = ModuleDefinitions(local_names, module_or_package_name)
        new_module_definitions.is_init = is_init
        self.module_definitions_stack.append(new_module_definitions)

        # Analyse the file
        self.filenames.append(module_path)
        self.local_modules = get_directory_modules(module_path) if self._allow_local_modules else []
        tree = generate_ast(module_path)

        # module[0] is None during e.g. "from . import foo", so we must str()
        self.nodes.append(EntryOrExitNode('Module Entry ' + str(module[0])))
        self.visit(tree)
        exit_node = self.append_node(EntryOrExitNode('Module Exit ' + str(module[0])))

        # Done analysing, pop the module off
        self.module_definitions_stack.pop()
        self.filenames.pop()

        if new_module_definitions.is_init:
            for def_ in new_module_definitions.definitions:
                module_def_alias = handle_aliases_in_init_files(
                    def_.name,
                    new_module_definitions.import_alias_mapping
                )
                parent_def_alias = handle_aliases_in_init_files(
                    def_.name,
                    parent_definitions.import_alias_mapping
                )
                # They should never both be set
                assert not (module_def_alias and parent_def_alias)

                def_name = def_.name
                if parent_def_alias:
                    def_name = parent_def_alias
                if module_def_alias:
                    def_name = module_def_alias

                local_definitions = self.module_definitions_stack[-1]
                if local_definitions != parent_definitions:
                    raise
                if not isinstance(module_or_package_name, str):
                    module_or_package_name = module_or_package_name.name

                if module_or_package_name:
                    if from_from:
                        qualified_name = def_name

                        if from_fdid:
                            alias = handle_fdid_aliases(module_or_package_name, import_alias_mapping)
                            if alias:
                                module_or_package_name = alias
                            parent_definition = ModuleDefinition(
                                parent_definitions,
                                qualified_name,
                                module_or_package_name,
                                self.filenames[-1]
                            )
                        else:
                            parent_definition = ModuleDefinition(
                                parent_definitions,
                                qualified_name,
                                None,
                                self.filenames[-1]
                            )
                    else:
                        qualified_name = module_or_package_name + '.' + def_name
                        parent_definition = ModuleDefinition(
                            parent_definitions,
                            qualified_name,
                            parent_definitions.module_name,
                            self.filenames[-1]
                        )
                    parent_definition.node = def_.node
                    parent_definitions.definitions.append(parent_definition)
                else:
                    parent_definition = ModuleDefinition(
                        parent_definitions,
                        def_name,
                        parent_definitions.module_name,
                        self.filenames[-1]
                    )
                    parent_definition.node = def_.node
                    parent_definitions.definitions.append(parent_definition)

        return exit_node

    def from_directory_import(
        self,
        module,
        real_names,
        local_names,
        import_alias_mapping,
        skip_init=False
    ):
        """
            Directories don't need to be packages.
        """
        module_path = module[1]

        init_file_location = os.path.join(module_path, '__init__.py')
        init_exists = os.path.isfile(init_file_location)

        if init_exists and not skip_init:
            package_name = os.path.split(module_path)[1]
            return self.add_module(
                module=(module[0], init_file_location),
                module_or_package_name=package_name,
                local_names=local_names,
                import_alias_mapping=import_alias_mapping,
                is_init=True,
                from_from=True
            )
        for real_name in real_names:
            full_name = os.path.join(module_path, real_name)
            if os.path.isdir(full_name):
                new_init_file_location = os.path.join(full_name, '__init__.py')
                if os.path.isfile(new_init_file_location):
                    self.add_module(
                        module=(real_name, new_init_file_location),
                        module_or_package_name=real_name,
                        local_names=local_names,
                        import_alias_mapping=import_alias_mapping,
                        is_init=True,
                        from_from=True,
                        from_fdid=True
                    )
                else:
                    raise Exception('from anything import directory needs an __init__.py file in directory')
            else:
                file_module = (real_name, full_name + '.py')
                self.add_module(
                    module=file_module,
                    module_or_package_name=real_name,
                    local_names=local_names,
                    import_alias_mapping=import_alias_mapping,
                    from_from=True
                )
        return IgnoredNode()

    def import_package(self, module, module_name, local_name, import_alias_mapping):
        module_path = module[1]
        init_file_location = os.path.join(module_path, '__init__.py')
        init_exists = os.path.isfile(init_file_location)
        if init_exists:
            return self.add_module(
                module=(module[0], init_file_location),
                module_or_package_name=module_name,
                local_names=local_name,
                import_alias_mapping=import_alias_mapping,
                is_init=True
            )
        else:
            raise Exception('import directory needs an __init__.py file')

    def handle_relative_import(self, node):
        """
            from A means node.level == 0
            from . import B means node.level == 1
            from .A means node.level == 1
        """
        no_file = os.path.abspath(os.path.join(self.filenames[-1], os.pardir))
        skip_init = False

        if node.level == 1:
            # Same directory as current file
            if node.module:
                name_with_dir = os.path.join(no_file, node.module.replace('.', '/'))
                if not os.path.isdir(name_with_dir):
                    name_with_dir = name_with_dir + '.py'
            # e.g. from . import X
            else:
                name_with_dir = no_file
                # We do not want to analyse the init file of the current directory
                skip_init = True
        else:
            parent = os.path.abspath(os.path.join(no_file, os.pardir))
            if node.level > 2:
                # Perform extra `cd ..` however many times
                for _ in range(0, node.level - 2):
                    parent = os.path.abspath(os.path.join(parent, os.pardir))
            if node.module:
                name_with_dir = os.path.join(parent, node.module.replace('.', '/'))
                if not os.path.isdir(name_with_dir):
                    name_with_dir = name_with_dir + '.py'
            # e.g. from .. import X
            else:
                name_with_dir = parent

        # Is it a file?
        if name_with_dir.endswith('.py'):
            return self.add_module(
                module=(node.module, name_with_dir),
                module_or_package_name=None,
                local_names=as_alias_handler(node.names),
                import_alias_mapping=retrieve_import_alias_mapping(node.names),
                from_from=True
            )
        return self.from_directory_import(
            (node.module, name_with_dir),
            not_as_alias_handler(node.names),
            as_alias_handler(node.names),
            retrieve_import_alias_mapping(node.names),
            skip_init=skip_init
        )

    def visit_Import(self, node):
        for name in node.names:
            for module in self.local_modules:
                if name.name == module[0]:
                    if os.path.isdir(module[1]):
                        return self.import_package(
                            module,
                            name,
                            name.asname,
                            retrieve_import_alias_mapping(node.names)
                        )
                    return self.add_module(
                        module=module,
                        module_or_package_name=name.name,
                        local_names=name.asname,
                        import_alias_mapping=retrieve_import_alias_mapping(node.names)
                    )
            for module in self.project_modules:
                if name.name == module[0]:
                    if os.path.isdir(module[1]):
                        return self.import_package(
                            module,
                            name,
                            name.asname,
                            retrieve_import_alias_mapping(node.names)
                        )
                    return self.add_module(
                        module=module,
                        module_or_package_name=name.name,
                        local_names=name.asname,
                        import_alias_mapping=retrieve_import_alias_mapping(node.names)
                    )
        for alias in node.names:
            # The module is uninspectable (so blackbox or built-in). If it has an alias, we remember
            # the alias so we can do fully qualified name resolution for blackbox- and built-in trigger words
            # e.g. we want a call to "os.system" be recognised, even if we do "import os as myos"
            if alias.asname is not None and alias.asname != alias.name:
                local_definitions = self.module_definitions_stack[-1]
                local_definitions.import_alias_mapping[name.asname] = alias.name
            if alias.name not in uninspectable_modules:
                log.warning("Cannot inspect module %s", alias.name)
                uninspectable_modules.add(alias.name)  # Don't repeatedly warn about this
        return IgnoredNode()

    def visit_ImportFrom(self, node):
        # Is it relative?
        if node.level > 0:
            return self.handle_relative_import(node)
        # not relative
        for module in self.local_modules:
            if node.module == module[0]:
                if os.path.isdir(module[1]):
                    return self.from_directory_import(
                        module,
                        not_as_alias_handler(node.names),
                        as_alias_handler(node.names)
                    )
                return self.add_module(
                    module=module,
                    module_or_package_name=None,
                    local_names=as_alias_handler(node.names),
                    import_alias_mapping=retrieve_import_alias_mapping(node.names),
                    from_from=True
                )
        for module in self.project_modules:
            name = module[0]
            if node.module == name:
                if os.path.isdir(module[1]):
                    return self.from_directory_import(
                        module,
                        not_as_alias_handler(node.names),
                        as_alias_handler(node.names),
                        retrieve_import_alias_mapping(node.names)
                    )
                return self.add_module(
                    module=module,
                    module_or_package_name=None,
                    local_names=as_alias_handler(node.names),
                    import_alias_mapping=retrieve_import_alias_mapping(node.names),
                    from_from=True
                )

        # Remember aliases for uninspectable modules such that we can label them fully qualified
        # e.g. we want a call to "os.system" be recognised, even if we do "from os import system"
        # from os import system as mysystem -> module=os, name=system, asname=mysystem
        for name in node.names:
            local_definitions = self.module_definitions_stack[-1]
            local_definitions.import_alias_mapping[name.asname or name.name] = "{}.{}".format(node.module, name.name)

        if node.module not in uninspectable_modules:
            log.warning("Cannot inspect module %s", node.module)
            uninspectable_modules.add(node.module)
        return IgnoredNode()
