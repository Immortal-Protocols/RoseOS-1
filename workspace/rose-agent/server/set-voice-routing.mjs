import os from 'node:os';
import {VoiceRouting} from './voice-routing.mjs';
const mode=process.argv[2];
const result=new VoiceRouting(os.homedir()+'/.config/pixel-voice/voice-routing.json').set(mode);
console.log(`Voice routing: ${result.mode}. Reconnect Rose Agent to apply to the call.`);
