package com.android.quickstep.fallback;
public final class RoseBackgroundActivityTest {
 static int checks;
 static void expect(boolean ok,String message){checks++;if(!ok)throw new AssertionError(message);}
 public static void main(String[] args){
  RoseBackgroundActivity m=new RoseBackgroundActivity();
  expect(m.frame(100,true)==0&&!m.animating(),"idle");
  m.accept("4:working:3100",4,100);m.frame(100,true);
  expect(m.frame(700,true)>.59f,"bounded movement");
  m.accept("4:working:3100",4,700);
  expect(m.frame(3101,true)==0&&!m.animating(),"duplicate cannot renew");
  m.accept("4:working:7000",4,4000);m.frame(4000,true);
  m.accept("4:terminal:7600",4,4600);
  expect(m.frame(4600,true)>.59f,"completion settles");
  expect(m.frame(4840,true)==0&&!m.animating(),"settle ends");
  m.accept("4:working:8000",4,5000);m.frame(5000,true);
  m.accept("",4,5500);expect(m.frame(5500,true)==0,"disconnect immediately still");
  for(String value:new String[]{null,"bad","4:working:999999","3:working:9000","4:working:-1","4:waiting:9000","4:working:9000:extra"}){
   m.accept(value,4,6000);expect(m.frame(6000,true)==0&&!m.animating(),"reject malformed/stale/other boot");
  }
  m.accept("4:working:10000",4,7000);expect(m.frame(7000,false)==0&&!m.animating(),"reduced/hidden");
  m.frame(7100,true);expect(m.frame(7700,true)>.59f,"resume restarts gently");
  m.frame(7700,false);m.accept("4:terminal:10000",4,7800);
  expect(m.frame(7800,true)==0&&!m.animating(),"no completion for unseen work");
  System.out.println("RoseBackgroundActivityTest: "+checks+" checks passed");
 }
}
