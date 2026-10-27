package dev.pixel.voice;
import java.util.*;
public class GestureTraceTest {
 static long time=1000;
 static void check(boolean value){if(!value)throw new AssertionError();}
 public static void main(String[] args){
  GestureTrace trace=new GestureTrace(()->time,rows->{});
  String a=trace.begin(Collections.singletonMap("action","touch_label"));
  time+=70;trace.event(a,"completed",Collections.emptyMap());
  trace.event(a,"queued",Collections.emptyMap());
  Map<String,Object> record=trace.snapshot().get(0);
  check(record.get("phase").equals("completed"));check(record.get("elapsedMs").equals(70L));
  String b=trace.begin(Collections.singletonMap("action","touch_label"));
  trace.event(b,"queued",Collections.emptyMap());time+=20;
  trace.event(b,"cancelled",Collections.emptyMap());check(trace.snapshot().get(1).get("phase").equals("cancelled"));
  for(int i=0;i<12;i++){time++;trace.observe(Collections.singletonMap("screenHash","abc"));}
  @SuppressWarnings("unchecked") List<Map<String,Object>> observations=(List<Map<String,Object>>)trace.snapshot().get(1).get("observations");
  check(observations.size()==8);observations.clear();
  check(((List<?>)trace.snapshot().get(1).get("observations")).size()==8);
  for(int i=0;i<30;i++)trace.begin(Collections.emptyMap());check(trace.snapshot().size()==24);
  trace.event(a,"cancelled",Collections.emptyMap());check(trace.snapshot().size()==24);
  GestureTrace failing=new GestureTrace(()->time,rows->{throw new RuntimeException("disk full");});
  String c=failing.begin(Collections.emptyMap());failing.event(c,"rejected",Collections.emptyMap());
  check(failing.snapshot().get(0).get("phase").equals("rejected"));
  System.out.println("Gesture completion, cancellation, bounds, and diagnostic isolation passed");
 }
}
