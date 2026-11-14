package dev.pixel.voice;

import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import rose.models.composer.LocalInference;

/**
 * Project 2: the model slot (WorkspaceModels), with fake loaders only. No model, runtime or weights are loaded.
 * The fake binding is NOT A MODEL: it only reports an id, and generates nothing here.
 */
public final class WorkspaceModelsTest {
 static int checks;
 static void expect(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}

 /** A fake binding that only reports the info a real binding would report. */
 static LocalInference fake(final String id){
  return new LocalInference(){
   @Override public Map<String,String> info(){Map<String,String> m=new LinkedHashMap<>();if(id!=null)m.put("id",id);m.put("runtime","fake");return m;}
   @Override public Generation generate(Request request){throw new AssertionError("the slot never generates");}
  };
 }
 static WorkspaceModels.Loader loader(final String name,final LocalInference inference,final Exception error){
  return new WorkspaceModels.Loader(){
   public String name(){return name;}
   public LocalInference load()throws Exception{if(error!=null)throw error;return inference;}
  };
 }

 public static void main(String[] args){
  shipped();none();ready();failsClosed();order();once();
  System.out.println("WorkspaceModelsTest: "+checks+" checks passed (fake loaders; no model loaded)");
 }

 /** The source ships no model: the slot is the starter until a binding is added to LOADERS. */
 static void shipped(){
  expect(WorkspaceModels.LOADERS.isEmpty(),"no model binding is in source today");
 }

 static void none(){
  WorkspaceModels.Slot s=new WorkspaceModels.Slot(new WorkspaceStarter(),Collections.<WorkspaceModels.Loader>emptyList());
  expect(s.status()==WorkspaceModels.Status.STARTER&&WorkspaceStarter.LABEL.equals(s.label()),"no loader: the starter, labelled NOT A MODEL");
  s.load();
  expect(s.status()==WorkspaceModels.Status.STARTER&&WorkspaceStarter.ID.equals(s.engine().modelId()),"loading with no loader changes nothing");
  expect(!WorkspaceTiming.isModel(s.engine().modelId()),"so prompt is not offered");
 }

 static void ready(){
  WorkspaceModels.Slot s=new WorkspaceModels.Slot(new WorkspaceStarter(),Arrays.asList(loader("phone-runtime",fake("small-v1"),null)));
  expect(s.status()==WorkspaceModels.Status.LOADING&&WorkspaceModels.LOADING_LABEL.equals(s.label()),"loading: says so");
  expect(WorkspaceStarter.ID.equals(s.engine().modelId()),"while loading the starter composes; nothing waits on the model");
  s.load();
  expect(s.status()==WorkspaceModels.Status.READY&&"small-v1".equals(s.engine().modelId())&&"phone-runtime".equals(s.loaded()),"ready: the model composes");
  expect(WorkspaceTiming.isModel(s.engine().modelId())&&WorkspaceModels.READY_LABEL.equals(s.label()),"a real model: prompt may be offered, and the label names the composer in use");
 }

 static void failsClosed(){
  WorkspaceModels.Slot thrown=new WorkspaceModels.Slot(new WorkspaceStarter(),Arrays.asList(loader("phone-runtime",null,new java.io.IOException("/data/secret/path weights.bin"))));
  thrown.load();
  expect(thrown.status()==WorkspaceModels.Status.FAILED&&WorkspaceStarter.ID.equals(thrown.engine().modelId()),"a failed load falls back to the starter");
  expect("phone-runtime: IOException".equals(thrown.failure())&&!thrown.failure().contains("secret"),"only the class name is kept, never the message: "+thrown.failure());
  expect(WorkspaceModels.FAILED_LABEL.equals(thrown.label()),"and the label says the model is unavailable");
  for(String id:new String[]{"starter","scripted",null,""}){
   WorkspaceModels.Slot s=new WorkspaceModels.Slot(new WorkspaceStarter(),Arrays.asList(loader("x",fake(id),null)));s.load();
   expect(s.status()==WorkspaceModels.Status.FAILED&&"x: not_a_model".equals(s.failure())&&!WorkspaceTiming.isModel(s.engine().modelId()),"never a model: id "+id);
  }
  WorkspaceModels.Slot nul=new WorkspaceModels.Slot(new WorkspaceStarter(),Arrays.asList(loader("x",null,null)));nul.load();
  expect(nul.status()==WorkspaceModels.Status.FAILED,"a loader that gives nothing fails closed");
  WorkspaceModels.Slot err=new WorkspaceModels.Slot(new WorkspaceStarter(),Arrays.asList(new WorkspaceModels.Loader(){
   public String name(){return "oom";}
   public LocalInference load(){throw new OutOfMemoryError("weights");}
  }));
  err.load();
  expect(err.status()==WorkspaceModels.Status.FAILED&&"oom: OutOfMemoryError".equals(err.failure()),"an Error while loading fails closed too");
 }

 static void order(){
  WorkspaceModels.Slot s=new WorkspaceModels.Slot(new WorkspaceStarter(),Arrays.asList(
   loader("first",null,new IllegalStateException("no runtime")),loader("second",fake("small-v2"),null),loader("third",fake("other"),null)));
  s.load();
  expect(s.status()==WorkspaceModels.Status.READY&&"second".equals(s.loaded())&&"small-v2".equals(s.engine().modelId())&&s.failure()==null,"the first loader that gives a model wins, in order");
 }

 static void once(){
  final int[] calls={0};
  WorkspaceModels.Slot s=new WorkspaceModels.Slot(new WorkspaceStarter(),Arrays.asList(new WorkspaceModels.Loader(){
   public String name(){return "counted";}
   public LocalInference load(){calls[0]++;return fake("small-v1");}
  }));
  s.load();s.load();
  expect(calls[0]==1,"a model is loaded once");
 }
}
