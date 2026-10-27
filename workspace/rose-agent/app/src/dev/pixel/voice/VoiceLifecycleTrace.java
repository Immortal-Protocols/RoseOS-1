package dev.pixel.voice;
import android.content.Context;
import android.media.*;
import org.json.*;
import java.util.ArrayDeque;

/** Bounded state transitions only: no transcripts, audio, or credentials. */
final class VoiceLifecycleTrace {
 private final VoiceService service;
 private final ArrayDeque<JSONObject> events=new ArrayDeque<>();
 VoiceLifecycleTrace(VoiceService service){this.service=service;
  try{JSONArray saved=new JSONArray(service.getSharedPreferences("voice-lifecycle",0).getString("events","[]"));for(int i=0;i<saved.length();i++)events.add(saved.getJSONObject(i));}catch(Exception ignored){}
  record("service_created","");
 }
 synchronized void record(String kind,String detail){try{
  JSONObject event=new JSONObject().put("at",System.currentTimeMillis()).put("kind",kind).put("detail",detail)
   .put("connected",service.connected).put("communicating",service.communicating).put("muted",service.muted).put("routeGeneration",service.routeGeneration);
  if(service.audio!=null){AudioDeviceInfo route=service.audio.getCommunicationDevice();event.put("routeType",route==null?0:route.getType()).put("audioMode",service.audio.getMode());
   JSONArray recordings=new JSONArray();for(AudioRecordingConfiguration r:service.audio.getActiveRecordingConfigurations())recordings.put(new JSONObject().put("silenced",r.isClientSilenced()).put("source",r.getClientAudioSource()).put("routeType",r.getAudioDevice()==null?0:r.getAudioDevice().getType()));event.put("recordings",recordings);
  }
  events.add(event);while(events.size()>64)events.removeFirst();
  service.getSharedPreferences("voice-lifecycle",0).edit().putString("events",snapshot().toString()).apply();
 }catch(Exception ignored){}
 }
 synchronized JSONArray snapshot(){JSONArray result=new JSONArray();for(JSONObject e:events)result.put(e);return result;}
}
