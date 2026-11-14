package dev.pixel.voice;

import java.util.Collections;
import java.util.List;
import java.util.Map;
import rose.models.composer.LocalInference;

/**
 * Project 2: the phone's model slot, where an on-phone model's binding plugs into the workspace without any change
 * to the page, the composer or the provider. The model and its runtime binding (a LocalInference) are the model
 * worker's. This side owns only choosing, loading and labelling it.
 *
 * <ul>
 *   <li><b>None today.</b> LOADERS is empty, so the slot uses WorkspaceStarter, which is NOT A MODEL. A binding is
 *   added by one line in LOADERS, and nothing else changes.</li>
 *   <li><b>Loaded off the main thread, once.</b> Until a model is ready, the starter composes, and the label says
 *   so. Loading never blocks composing.</li>
 *   <li><b>Fails closed.</b> A loader that throws, or reports no id, or reports the starter's or the scripted
 *   test double's id, never becomes the model. The slot falls back to the starter and records only the
 *   failure's class name, never its message.</li>
 *   <li><b>Labelled.</b> The page's label names the composer in use, not the layout on screen. A layout composed
 *   earlier keeps its own record in WorkspaceTiming.</li>
 * </ul>
 * Pure (no Android types).
 */
final class WorkspaceModels {
 private WorkspaceModels() {}

 /** One on-phone model binding. load() may be slow (it reads weights); it runs on the slot's own thread. */
 interface Loader {
  /** A short, stable name for logs and the failure record (no path or secret). */
  String name();
  LocalInference load() throws Exception;
 }

 /** The phone's model bindings, most preferred first. Empty until the model worker's binding is in source. */
 static final List<Loader> LOADERS=Collections.emptyList();

 enum Status { STARTER, LOADING, READY, FAILED }

 static final String LOADING_LABEL="Starter layout · on-phone model loading";
 static final String FAILED_LABEL="Starter layout · on-phone model unavailable";
 static final String READY_LABEL="Arranged with the on-phone model";

 /** The slot: the starter until a loader gives a real model. Safe to read from any thread. */
 static final class Slot {
  private final WorkspaceEngine starter;
  private final List<Loader> loaders;
  private volatile WorkspaceEngine model;
  private volatile Status status;
  private volatile String failure,loaded;
  private boolean started;

  Slot(LocalInference starter,List<Loader> loaders){
   this.starter=new WorkspaceEngine(starter);
   this.loaders=loaders==null?Collections.<Loader>emptyList():loaders;
   status=this.loaders.isEmpty()?Status.STARTER:Status.LOADING;
  }

  /**
   * Tries each loader in order, once, on the calling thread (the hub's loader thread, never the main thread).
   * The first one that gives a real model wins. A second call does nothing.
   */
  void load(){
   synchronized(this){if(started)return;started=true;}
   if(loaders.isEmpty())return;
   String last=null;
   for(Loader l:loaders){
    String name;try{name=l.name();}catch(RuntimeException e){name="loader";}
    try{
     LocalInference inference=l.load();
     String id=id(inference);
     if(!WorkspaceTiming.isModel(id)){last=name+": not_a_model";continue;}
     model=new WorkspaceEngine(inference);loaded=name;failure=null;status=Status.READY;return;
    }catch(Throwable t){last=name+": "+t.getClass().getSimpleName();}
   }
   failure=last;status=Status.FAILED;
  }

  /** The engine to compose with now: the model once ready, otherwise the starter. */
  WorkspaceEngine engine(){WorkspaceEngine m=model;return m!=null?m:starter;}
  Status status(){return status;}
  /** Why loading failed (loader name and exception class only), or null. */
  String failure(){return failure;}
  /** The loader that gave the model, or null. */
  String loaded(){return loaded;}

  /** The page's label for the composer in use. */
  String label(){
   switch(status){
    case READY:return READY_LABEL;
    case LOADING:return LOADING_LABEL;
    case FAILED:return FAILED_LABEL;
    default:return WorkspaceStarter.LABEL;
   }
  }
 }

 private static String id(LocalInference inference){
  if(inference==null)return null;
  try{Map<String,String> info=inference.info();return info==null?null:info.get("id");}catch(RuntimeException e){return null;}
 }
}
