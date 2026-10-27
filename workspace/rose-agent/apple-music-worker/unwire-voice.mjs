// Explicit rollback of only this voice-tool integration; the native worker stays installed.
import fs from 'node:fs';import {elevenRequest,agentId} from '../server/elevenlabs.mjs';
const s=JSON.parse(fs.readFileSync(process.env.HOME+'/.config/pixel-voice/apple-music-worker/voice-tool.json','utf8'));
const a=await elevenRequest('/convai/agents/'+agentId);const p=a.conversation_config.agent.prompt;
const prompt=p.prompt.replace(/\n*\[APPLE_MUSIC_LOCAL_TRIAL\][\s\S]*?\[\/APPLE_MUSIC_LOCAL_TRIAL\]/,'').trim();
await elevenRequest('/convai/agents/'+agentId,{method:'PATCH',body:{conversation_config:{agent:{prompt:{prompt,tool_ids:(p.tool_ids||[]).filter(id=>id!==s.toolId)}}}}});
console.log('Apple Music voice-tool wiring removed; other prompt text and tools preserved. Start a new voice session.');
