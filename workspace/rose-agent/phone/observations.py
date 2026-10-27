"""Compact, explicit observations for the model; never change node IDs or labels."""
def compact_result(result):
    if not isinstance(result, dict):
        return result
    if 'nodes' not in result:
        return {key: compact_result(value) if key == 'observation' else value for key, value in result.items()}
    out = {key: result[key] for key in ('status', 'packageName', 'snapshotId', 'validForMs', 'truncated', 'visualCheck', 'verificationRequired', 'backgroundExecution', 'backgroundVerified', 'sessionId', 'displayId', 'scope') if key in result}
    rows = []
    for node in result['nodes']:
        if node.get('visible') is False:
            continue
        actions = [name for key, name in (('clickable','tap'),('longClickable','long_press'),('editable','set_text'),('imeEnter','ime_enter'),('scrollable','scroll')) if node.get(key)]
        bounds=node.get('bounds')
        if bounds and node.get('enabled',True) and not node.get('password') and bounds[2]-bounds[0]>=64 and bounds[3]-bounds[1]>=64:
            actions.extend(['drag','touch_point'])
        if not (actions or node.get('text') or node.get('description')):
            continue
        row = {'id': node['id']}
        for key in ('text', 'description'):
            if node.get(key) and not (key == 'description' and node.get(key) == node.get('text')):
                row[key] = node[key]
        if 'clickTarget' in node and (node.get('text') or node.get('description')):
            row['clickTarget'] = node['clickTarget']
        if actions:
            row['actions'] = actions
        if 'drag' in actions and node.get('class'):
            row['class']=node['class']
        if not node.get('enabled', True):
            row['enabled'] = False
        if node.get('editable') and 'focused' in node:
            row['focused'] = node['focused']
        if node.get('password'):
            row['password'] = True
        if node.get('checked'):
            row['checked'] = True
        if node.get('selected'):
            row['selected'] = True
        # Bounds remain available for identifying unlabelled containers and their children.
        if actions and node.get('bounds') or not node.get('text') and not node.get('description'):
            row['bounds'] = node.get('bounds')
        rows.append(row)
    out['nodes'] = rows
    return out


def compact_command(command):
    return {key: compact_result(value) if key == 'result' else value for key, value in command.items()
            if key in ('id','action','status','result')}
