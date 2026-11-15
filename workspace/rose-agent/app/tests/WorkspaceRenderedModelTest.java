package dev.pixel.voice;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.List;
import java.util.Map;
import rose.models.composer.Json;

/** Replays real server smoke results through the phone's production validator and persistent state. */
public final class WorkspaceRenderedModelTest {
 @SuppressWarnings("unchecked") public static void main(String[] args)throws Exception{
  Map<String,Object> snapshot=(Map<String,Object>)Json.parse(new String(Files.readAllBytes(Paths.get(args[1])),StandardCharsets.UTF_8));
  WorkspaceLayout.Registry registry=new WorkspaceLayout.Registry((String)snapshot.get("snapshotVersion"));
  for(String kind:new String[]{"apps","notifications","content","actions"})for(Object item:(List<Object>)snapshot.get(kind)){
   Map<String,Object> e=(Map<String,Object>)item;String id=(String)e.get("id");
   if(kind.equals("actions"))registry.action(id,(String)e.get("kind"),(String)e.get("targetId"));else registry.add(kind,id);
  }
  int checks=0;
  for(String result:Files.readAllLines(Paths.get(args[0]),StandardCharsets.UTF_8)){
   Map<String,Object> r=(Map<String,Object>)Json.parse(result);
   WorkspaceState state=new WorkspaceState();WorkspaceComposer composer=new WorkspaceComposer(state);
   int gen=composer.begin("real model fixture",true,(String)r.get("requestId"));
   if(composer.finish(gen,result,registry)!=WorkspaceComposer.Outcome.COMMITTED)throw new AssertionError("real server layout refused");checks++;
   if(WorkspaceState.fromJson(state.toJson()).layout()==null)throw new AssertionError("real model layout not retained");checks++;
  }
  if(checks!=12)throw new AssertionError("six actual model results required");
  System.out.println("WorkspaceRenderedModelTest: "+checks+" checks passed; production renderer data and persistence, no phone draw");
 }
}
