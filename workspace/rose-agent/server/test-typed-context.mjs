import fs from 'node:fs';
import OpenAI from 'openai';
import {TypedConversation} from './conversation.mjs';
const c=new TypedConversation();c.input('Draft an email to someone@example.com. Do not send it.');c.add('assistant','What should the email say?');
const client=new OpenAI({apiKey:fs.readFileSync((process.env.HOME + "/.config/pixel-voice/openai-api-key"),'utf8').trim()});
const r=await client.responses.create({model:'gpt-6-luna',store:false,input:c.input('Hi'),reasoning:{effort:'low'},max_output_tokens:200});
console.log(r.output_text);
