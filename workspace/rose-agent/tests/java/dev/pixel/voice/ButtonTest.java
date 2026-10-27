package dev.pixel.voice;
public class ButtonTest {
 static void check(boolean b){if(!b)throw new AssertionError();}
 static {GlassesButtonGate g=new GlassesButtonGate();check(g.accept(127,0,0,0,10000));check(!g.accept(127,1,0,0,10006));check(g.accept(127,0,0,0,11792));check(!g.accept(127,1,0,0,11798));check(g.accept(127,1,0,0,13000));check(!g.accept(127,1,0,0,13005));}
 public static void main(String[] args){GlassesButtonGate g=new GlassesButtonGate();check(g.accept(127,0,0,10,10));check(!g.accept(127,1,0,10,40));check(!g.accept(127,0,1,10,60));check(g.accept(127,1,0,200,240));check(!g.accept(127,1,0,200,260));check(g.accept(127,0,0,1000,1000));System.out.println("PASS: normal pairs, release-only taps, duplicates, and repeats");}
}
