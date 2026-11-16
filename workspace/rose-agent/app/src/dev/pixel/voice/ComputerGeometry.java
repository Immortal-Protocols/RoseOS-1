package dev.pixel.voice;

/**
 * Computer Link (docs/COMPUTER-LINK.md): the size of the straightened screen frame made from the four
 * calibrated corners of the computer's monitor in a camera photo. Pure Java, unit-tested.
 */
final class ComputerGeometry {
 private ComputerGeometry(){}
 static final int FRAME_LONG_EDGE=1920;
 private static final double[] COMMON={16.0/9,16.0/10,21.0/9,4.0/3,3.0/2,32.0/9};

 /**
  * corners: x,y pixel pairs in the order top-left, top-right, bottom-right, bottom-left of the monitor as
  * the camera sees it. Returns {width,height}: the measured aspect (averaged opposite edges, snapped to a
  * common monitor shape within 8%, since perspective foreshortens it) at FRAME_LONG_EDGE.
 */
 static int[] frameSize(float[] corners){
  if(corners==null||corners.length!=8)throw new IllegalArgumentException("Four corners are required");
  double top=dist(corners,0,1),right=dist(corners,1,2),bottom=dist(corners,2,3),left=dist(corners,3,0);
  if(Math.min(Math.min(top,bottom),Math.min(left,right))<20)throw new IllegalArgumentException("Corners are too close together");
  if(!convex(corners))throw new IllegalArgumentException("Corners must go around the screen in order");
  double aspect=snap((top+bottom)/(left+right));
  return aspect>=1?new int[]{FRAME_LONG_EDGE,(int)Math.round(FRAME_LONG_EDGE/aspect)}:new int[]{(int)Math.round(FRAME_LONG_EDGE*aspect),FRAME_LONG_EDGE};
 }

 static double snap(double aspect){
  for(double c:COMMON){
   if(Math.abs(aspect-c)/c<=0.08)return c;
   if(Math.abs(aspect-1/c)*c<=0.08)return 1/c;
  }
  return aspect;
 }

 private static double dist(float[] p,int a,int b){return Math.hypot(p[2*a]-p[2*b],p[2*a+1]-p[2*b+1]);}

 /** The quadrilateral turns the same way at every corner and does not cross itself. */
 static boolean convex(float[] p){
  int sign=0;
  for(int i=0;i<4;i++){
   int a=i,b=(i+1)%4,c=(i+2)%4;
   double cross=(p[2*b]-p[2*a])*(p[2*c+1]-p[2*b+1])-(p[2*b+1]-p[2*a+1])*(p[2*c]-p[2*b]);
   int s=cross>0?1:cross<0?-1:0;
   if(s==0)return false;
   if(sign==0)sign=s;else if(s!=sign)return false;
  }
  return true;
 }
}
