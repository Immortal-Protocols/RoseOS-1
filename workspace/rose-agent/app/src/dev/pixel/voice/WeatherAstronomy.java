/* Portions adapted from Stardroid v2 MeeusEphemeris.kt, Copyright (c) 2026
 * Penterakt LLC. GPL-3.0-or-later. See assets/licenses/weather-astronomy.txt.
 * Meeus, Astronomical Algorithms, chapters 25, 47, 48.
 */
package dev.pixel.voice;
import static java.lang.Math.*;

/** Offline geocentric phase + sea-level topocentric horizon geometry. No device timezone. */
final class WeatherAstronomy {
 static final double R=PI/180;
 static final class State {
  final double sunAltitude,sunAzimuth,moonAltitude,moonAzimuth,fraction,limbRotation;
  final boolean waxing;
  State(double sa,double sz,double ma,double mz,double f,double rot,boolean w){sunAltitude=sa;sunAzimuth=sz;moonAltitude=ma;moonAzimuth=mz;fraction=f;limbRotation=rot;waxing=w;}
 }
 static double clamp(double x,double lo,double hi){return max(lo,min(hi,x));}
 static double norm(double x){return (x%360+360)%360;}
 static double sind(double x){return sin(x*R);} static double cosd(double x){return cos(x*R);}
 static State at(long millis,double lat,double lon){
  if(!Double.isFinite(lat)||!Double.isFinite(lon)||abs(lat)>90||abs(lon)>180)throw new IllegalArgumentException("Coordinates required");
  double jd=millis/86400000.0+2440587.5;
  // TT-UTC = 69.184 seconds for the current leap-second era; documented accuracy range 2020–2035.
  double t=(jd+69.184/86400-2451545)/36525, t2=t*t,t3=t2*t,t4=t3*t;
  double l0=280.46646+36000.76983*t+.0003032*t2,m=357.52911+35999.05029*t-.0001537*t2;
  double c=(1.914602-.004817*t-.000014*t2)*sind(m)+(.019993-.000101*t)*sind(2*m)+.000289*sind(3*m);
  double omega=125.04-1934.136*t,sl=l0+c-.00569-.00478*sind(omega),eps=23.439291-.0130042*t+.00256*cosd(omega);
  double[] sun=equatorial(sl,0,eps);
  double lp=218.3164477+481267.88123421*t-.0015786*t2+t3/538841-t4/65194000;
  double d=297.8501921+445267.1114034*t-.0018819*t2+t3/545868-t4/113065000;
  double mp=134.9633964+477198.8675055*t+.0087414*t2+t3/69699-t4/14712000;
  double f=93.2720950+483202.0175233*t-.0036539*t2-t3/3526000+t4/863310000;
  double a1=119.75+131.849*t,a2=53.09+479264.290*t,a3=313.45+481266.484*t,e=1-.002516*t-.0000074*t2;
  double sumL=0,sumB=0,sumR=0;
  for(int[] row:A){double arg=row[0]*d+row[1]*m+row[2]*mp+row[3]*f,ef=pow(e,abs(row[1]));sumL+=row[4]*ef*sind(arg);sumR+=row[5]*ef*cosd(arg);}
  for(int[] row:B){double arg=row[0]*d+row[1]*m+row[2]*mp+row[3]*f;sumB+=row[4]*pow(e,abs(row[1]))*sind(arg);}
  sumL+=3958*sind(a1)+1962*sind(lp-f)+318*sind(a2);
  sumB+=-2235*sind(lp)+382*sind(a3)+175*sind(a1-f)+175*sind(a1+f)+127*sind(lp-mp)-115*sind(lp+mp);
  double om=125.04452-1934.136261*t,ls=280.4665+36000.7698*t,lm=218.3165+481267.8813*t;
  double psi=(-17.20*sind(om)-1.32*sind(2*ls)-.23*sind(2*lm)+.21*sind(2*om))/3600;
  double deps=(9.20*cosd(om)+.57*cosd(2*ls)+.10*cosd(2*lm)-.09*cosd(2*om))/3600;
  double ml=lp+sumL/1e6+psi,mb=sumB/1e6,dist=385000.56+sumR/1000;
  double[] moon=equatorial(ml,mb,23.4392911-.0130042*t-1.64e-7*t2+5.04e-7*t3+deps);
  double elong=acos(clamp(sin(sun[1])*sin(moon[1])+cos(sun[1])*cos(moon[1])*cos(sun[0]-moon[0]),-1,1));
  double phase=atan2(149598000*sin(elong),dist-149598000*cos(elong));
  double limb=atan2(cos(sun[1])*sin(sun[0]-moon[0]),sin(sun[1])*cos(moon[1])-cos(sun[1])*sin(moon[1])*cos(sun[0]-moon[0]));
  double ut=(jd-2451545)/36525,lst=norm(280.46061837+360.98564736629*(jd-2451545)+.000387933*ut*ut-ut*ut*ut/38710000+lon)*R;
  double[] sh=horizon(sun,lst,lat),top=topocentric(moon,dist,lst,lat),mh=horizon(top,lst,lat);
  double h=lst-top[0],q=atan2(sin(h),tan(lat*R)*cos(top[1])-sin(top[1])*cos(h));
  // Screen rotation: lit half starts on screen right; celestial east is screen left.
  return new State(sh[0],sh[1],mh[0],mh[1],(1+cos(phase))/2,(q-limb)/R-90,norm(ml-sl)<180);
 }
 static double[] equatorial(double l,double b,double ep){double x=cosd(b)*cosd(l),y=cosd(ep)*cosd(b)*sind(l)-sind(ep)*sind(b),z=sind(ep)*cosd(b)*sind(l)+cosd(ep)*sind(b);return new double[]{atan2(y,x),atan2(z,hypot(x,y))};}
 static double[] topocentric(double[] p,double distance,double lst,double lat){
  double phi=lat*R,u=atan(.99664719*tan(phi)),rhoCos=cos(u),rhoSin=.99664719*sin(u),r=distance/6378.14;
  double x=r*cos(p[1])*cos(p[0])-rhoCos*cos(lst),y=r*cos(p[1])*sin(p[0])-rhoCos*sin(lst),z=r*sin(p[1])-rhoSin;
  return new double[]{atan2(y,x),atan2(z,hypot(x,y))};
 }
 static double[] horizon(double[] p,double lst,double lat){double h=lst-p[0],phi=lat*R;return new double[]{asin(clamp(sin(phi)*sin(p[1])+cos(phi)*cos(p[1])*cos(h),-1,1))/R,norm(atan2(sin(h),cos(h)*sin(phi)-tan(p[1])*cos(phi))/R+180)};}
 static final int[][] A={
  {0, 0, 1, 0, 6288774, -20905355},
  {2, 0, -1, 0, 1274027, -3699111},
  {2, 0, 0, 0, 658314, -2955968},
  {0, 0, 2, 0, 213618, -569925},
  {0, 1, 0, 0, -185116, 48888},
  {0, 0, 0, 2, -114332, -3149},
  {2, 0, -2, 0, 58793, 246158},
  {2, -1, -1, 0, 57066, -152138},
  {2, 0, 1, 0, 53322, -170733},
  {2, -1, 0, 0, 45758, -204586},
  {0, 1, -1, 0, -40923, -129620},
  {1, 0, 0, 0, -34720, 108743},
  {0, 1, 1, 0, -30383, 104755},
  {2, 0, 0, -2, 15327, 10321},
  {0, 0, 1, 2, -12528, 0},
  {0, 0, 1, -2, 10980, 79661},
  {4, 0, -1, 0, 10675, -34782},
  {0, 0, 3, 0, 10034, -23210},
  {4, 0, -2, 0, 8548, -21636},
  {2, 1, -1, 0, -7888, 24208},
  {2, 1, 0, 0, -6766, 30824},
  {1, 0, -1, 0, -5163, -8379},
  {1, 1, 0, 0, 4987, -16675},
  {2, -1, 1, 0, 4036, -12831},
  {2, 0, 2, 0, 3994, -10445},
  {4, 0, 0, 0, 3861, -11650},
  {2, 0, -3, 0, 3665, 14403},
  {0, 1, -2, 0, -2689, -7003},
  {2, 0, -1, 2, -2602, 0},
  {2, -1, -2, 0, 2390, 10056},
  {1, 0, 1, 0, -2348, 6322},
  {2, -2, 0, 0, 2236, -9884},
  {0, 1, 2, 0, -2120, 5751},
  {0, 2, 0, 0, -2069, 0},
  {2, -2, -1, 0, 2048, -4950},
  {2, 0, 1, -2, -1773, 4130},
  {2, 0, 0, 2, -1595, 0},
  {4, -1, -1, 0, 1215, -3958},
  {0, 0, 2, 2, -1110, 0},
  {3, 0, -1, 0, -892, 3258},
  {2, 1, 1, 0, -810, 2616},
  {4, -1, -2, 0, 759, -1897},
  {0, 2, -1, 0, -713, -2117},
  {2, 2, -1, 0, -700, 2354},
  {2, 1, -2, 0, 691, 0},
  {2, -1, 0, -2, 596, 0},
  {4, 0, 1, 0, 549, -1423},
  {0, 0, 4, 0, 537, -1117},
  {4, -1, 0, 0, 520, -1571},
  {1, 0, -2, 0, -487, -1739},
  {2, 1, 0, -2, -399, 0},
  {0, 0, 2, -2, -381, -4421},
  {1, 1, 1, 0, 351, 0},
  {3, 0, -2, 0, -340, 0},
  {4, 0, -3, 0, 330, 0},
  {2, -1, 2, 0, 327, 0},
  {0, 2, 1, 0, -323, 1165},
  {1, 1, -1, 0, 299, 0},
  {2, 0, 3, 0, 294, 0},
  {2, 0, -1, -2, 0, 8752}
 };
 static final int[][] B={
  {0, 0, 0, 1, 5128122},
  {0, 0, 1, 1, 280602},
  {0, 0, 1, -1, 277693},
  {2, 0, 0, -1, 173237},
  {2, 0, -1, 1, 55413},
  {2, 0, -1, -1, 46271},
  {2, 0, 0, 1, 32573},
  {0, 0, 2, 1, 17198},
  {2, 0, 1, -1, 9266},
  {0, 0, 2, -1, 8822},
  {2, -1, 0, -1, 8216},
  {2, 0, -2, -1, 4324},
  {2, 0, 1, 1, 4200},
  {2, 1, 0, -1, -3359},
  {2, -1, -1, 1, 2463},
  {2, -1, 0, 1, 2211},
  {2, -1, -1, -1, 2065},
  {0, 1, -1, -1, -1870},
  {4, 0, -1, -1, 1828},
  {0, 1, 0, 1, -1794},
  {0, 0, 0, 3, -1749},
  {0, 1, -1, 1, -1565},
  {1, 0, 0, 1, -1491},
  {0, 1, 1, 1, -1475},
  {0, 1, 1, -1, -1410},
  {0, 1, 0, -1, -1344},
  {1, 0, 0, -1, -1335},
  {0, 0, 3, 1, 1107},
  {4, 0, 0, -1, 1021},
  {4, 0, -1, 1, 833},
  {0, 0, 1, -3, 777},
  {4, 0, -2, 1, 671},
  {2, 0, 0, -3, 607},
  {2, 0, 2, -1, 596},
  {2, -1, 1, -1, 491},
  {2, 0, -2, 1, -451},
  {0, 0, 3, -1, 439},
  {2, 0, 2, 1, 422},
  {2, 0, -3, -1, 421},
  {2, 1, -1, 1, -366},
  {2, 1, 0, 1, -351},
  {4, 0, 0, 1, 331},
  {2, -1, 1, 1, 315},
  {2, -2, 0, -1, 302},
  {0, 0, 1, 3, -283},
  {2, 1, 1, -1, -229},
  {1, 1, 0, -1, 223},
  {1, 1, 0, 1, 223},
  {0, 1, -2, -1, -220},
  {2, 1, -1, -1, -220},
  {1, 0, 1, 1, -185},
  {2, -1, -2, -1, 181},
  {0, 1, 2, 1, -177},
  {4, 0, -2, -1, 176},
  {4, -1, -1, -1, 166},
  {1, 0, 1, -1, -164},
  {4, 0, 1, -1, 132},
  {1, 0, -1, -1, -119},
  {4, -1, 0, -1, 115},
  {2, -2, 0, 1, 107}
 };
}
