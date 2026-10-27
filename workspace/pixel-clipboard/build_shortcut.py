#!/usr/bin/env python3
"""Generate the paired shortcut privately; credentials never enter source control."""
import json, plistlib, subprocess, uuid
from pathlib import Path
base = Path.home() / '.config/pixel-clipboard'
cfg = json.loads((base / 'config.json').read_text())
def text(value):
    return {'Value': {'string': value, 'attachmentsByRange': {}}, 'WFSerializationType': 'WFTextTokenString'}
def dictionary(values):
    return {'Value': {'WFDictionaryFieldValueItems': [
        {'WFItemType': 0, 'WFKey': text(k), 'WFValue': v} for k, v in values.items()
    ]}, 'WFSerializationType': 'WFDictionaryFieldValue'}
clip_id = str(uuid.uuid4()).upper()
clip = {'Value': {'string': '\ufffc', 'attachmentsByRange': {'{0, 1}': {
    'OutputUUID': clip_id, 'OutputName': 'Clipboard', 'Type': 'ActionOutput'
}}}, 'WFSerializationType': 'WFTextTokenString'}
workflow = {
    'WFWorkflowClientVersion': '4711', 'WFWorkflowMinimumClientVersion': 900,
    'WFWorkflowMinimumClientVersionString': '900', 'WFWorkflowName': 'Send to Pixel',
    'WFWorkflowIcon': {'WFWorkflowIconStartColor': 4282601983, 'WFWorkflowIconGlyphNumber': 59511},
    'WFWorkflowTypes': ['NCWidget'], 'WFWorkflowInputContentItemClasses': ['WFStringContentItem'],
    'WFWorkflowActions': [
        {'WFWorkflowActionIdentifier': 'is.workflow.actions.getclipboard', 'WFWorkflowActionParameters': {'UUID': clip_id}},
        {'WFWorkflowActionIdentifier': 'is.workflow.actions.downloadurl', 'WFWorkflowActionParameters': {
            'UUID': str(uuid.uuid4()).upper(), 'WFURL': text(f'https://{cfg["ip"]}:{cfg["port"]}/send'),
            'WFHTTPMethod': 'POST', 'WFHTTPBodyType': 'JSON',
            'WFHTTPHeaders': dictionary({'Authorization': text('Bearer ' + cfg['sendToken'])}),
            'WFJSONValues': dictionary({'text': clip})}},
        {'WFWorkflowActionIdentifier': 'is.workflow.actions.notification', 'WFWorkflowActionParameters': {
            'WFNotificationActionTitle': 'Copied to Pixel', 'WFNotificationActionBody': 'Ready to paste on your Pixel.',
            'WFNotificationActionSound': False}}
    ]
}
source = base / 'Send to Pixel.unsigned.shortcut'
source.write_bytes(plistlib.dumps(workflow)); source.chmod(0o600)
target = base / 'Send to Pixel.shortcut'
subprocess.run(['shortcuts', 'sign', '--mode', 'anyone', '--input', str(source), '--output', str(target)], check=True)
target.chmod(0o600)
print('Paired shortcut saved in private configuration directory.')
