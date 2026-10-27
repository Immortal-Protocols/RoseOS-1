package dev.pixel.voice;
import android.content.Context;
import android.content.SharedPreferences;
import org.json.*;
import java.nio.charset.StandardCharsets;
import java.util.regex.Pattern;

/**
 * One declarative home visual, kept in private preferences. Input is rebuilt field by field:
 * unknown keys are dropped, nothing is executed, fetched or estimated. A missing value stays missing.
 */
final class VisualStore {
 static final int MAX_BYTES=100*1024,MAX_DAYS=14,MAX_MEALS=50,MAX_NUTRIENTS=50;
 static final double MAX_VALUE=1e9;
 private static final String FILE="home-visual",VISUAL="visual",REVISION="revision",ENABLED="enabled",DISMISSED="dismissed",PINNED="pinned";
 private static final Pattern ID=Pattern.compile("[A-Za-z0-9._-]{1,64}"),DATE=Pattern.compile("\\d{4}-\\d{2}-\\d{2}"),TIME=Pattern.compile("([01]\\d|2[0-3]):[0-5]\\d"),STAMP=Pattern.compile("[0-9T:.Z+-]{4,40}");
 static final class Invalid extends Exception{Invalid(String message){super(message);}}

 private static SharedPreferences prefs(Context context){return context.getApplicationContext().getSharedPreferences(FILE,Context.MODE_PRIVATE);}

 /** Cheap enough for the home refresh tick; changes whenever the card needs rebuilding. */
 static long revision(Context context){return prefs(context).getLong(REVISION,0);}
 static boolean enabled(Context context){return prefs(context).getBoolean(ENABLED,true);}
 static boolean dismissed(Context context){return prefs(context).getBoolean(DISMISSED,false);}
 static boolean pinned(Context context){return prefs(context).getBoolean(PINNED,false);}
 static synchronized void setEnabled(Context context,boolean on){SharedPreferences p=prefs(context);p.edit().putBoolean(ENABLED,on).putLong(REVISION,p.getLong(REVISION,0)+1).apply();}
 static synchronized void setPinned(Context context,boolean on){SharedPreferences p=prefs(context);p.edit().putBoolean(PINNED,on).putLong(REVISION,p.getLong(REVISION,0)+1).apply();}
 static synchronized void dismiss(Context context){SharedPreferences p=prefs(context);p.edit().putBoolean(DISMISSED,true).putBoolean(PINNED,false).putLong(REVISION,p.getLong(REVISION,0)+1).apply();}

 /** The stored visual, or null when there is none or it cannot be read. */
 static synchronized JSONObject load(Context context){
  String raw=prefs(context).getString(VISUAL,null);if(raw==null)return null;
  try{return sanitize(new JSONObject(raw));}catch(Exception e){return null;}
 }
 /** The card the home screen should show right now, or null to leave home unchanged. */
 static JSONObject visible(Context context){return enabled(context)&&!dismissed(context)?load(context):null;}

 /** An explicit request to show a visual. Leaves the master toggle as the user set it. */
 static synchronized JSONObject set(Context context,Object raw)throws JSONException{
  JSONObject clean;
  try{if(!(raw instanceof JSONObject))throw new Invalid("visual must be an object");clean=sanitize((JSONObject)raw);}
  catch(Invalid e){return status(context).put("status","invalid").put("message",e.getMessage());}
  SharedPreferences p=prefs(context);JSONObject previous=load(context);
  boolean sameId=previous!=null&&previous.optString("id").equals(clean.optString("id"));
  if(!p.edit().putString(VISUAL,clean.toString()).putBoolean(DISMISSED,false).putBoolean(PINNED,sameId&&p.getBoolean(PINNED,false)).putLong(REVISION,p.getLong(REVISION,0)+1).commit())
   return status(context).put("status","error").put("message","Could not save visual");
  return status(context);
 }
 /** Removes the visual only. The master toggle and all other app data stay. */
 static synchronized JSONObject clear(Context context)throws JSONException{
  SharedPreferences p=prefs(context);
  if(!p.edit().remove(VISUAL).remove(DISMISSED).remove(PINNED).putLong(REVISION,p.getLong(REVISION,0)+1).commit())
   return status(context).put("status","error").put("message","Could not clear visual");
  return status(context);
 }
 /** Small summary read back from storage. Never contains meal or nutrient data. */
 static synchronized JSONObject status(Context context)throws JSONException{
  SharedPreferences p=prefs(context);JSONObject visual=load(context);boolean on=p.getBoolean(ENABLED,true);
  int dayCount=0,mealCount=0;
  if(visual!=null&&visual.optString("kind","meals").equals("meals")){JSONArray days=visual.getJSONArray("days");dayCount=days.length();for(int i=0;i<dayCount;i++)mealCount+=days.getJSONObject(i).getJSONArray("meals").length();}
  JSONObject result=new JSONObject().put("status","ok").put("id",visual==null?JSONObject.NULL:visual.getString("id")).put("dayCount",dayCount).put("mealCount",mealCount)
   .put("kind",visual==null?JSONObject.NULL:visual.optString("kind","meals"))
   .put("enabled",on).put("visible",on&&visual!=null&&!p.getBoolean(DISMISSED,false)).put("pinned",visual!=null&&p.getBoolean(PINNED,false)).put("revision",p.getLong(REVISION,0));
  return result;
 }

 /** Validates and returns a copy holding only the known fields. Error messages name fields, never values. */
 static JSONObject sanitize(JSONObject in)throws Invalid,JSONException{
  if(in.toString().getBytes(StandardCharsets.UTF_8).length>MAX_BYTES)throw new Invalid("visual is larger than 100 KB");
  JSONObject out=new JSONObject();
  out.put("id",match(text(in,"id",64,true,"id"),ID,"id"));
  out.put("title",text(in,"title",60,true,"title"));
  putIf(out,"subtitle",text(in,"subtitle",80,false,"subtitle"));
  putIf(out,"source",text(in,"source",40,false,"source"));
  putIf(out,"updatedAt",match(text(in,"updatedAt",40,false,"updatedAt"),STAMP,"updatedAt"));
  String kind=text(in,"kind",12,false,"kind");if(kind==null)kind="meals";
  if(!kind.equals("meals")&&!kind.equals("wind"))throw new Invalid("kind must be meals or wind");
  if(kind.equals("wind")){
   JSONObject wind=object(in.opt("wind"),"wind"),cleanWind=new JSONObject();
   cleanWind.put("origin",text(wind,"origin",50,true,"wind.origin"));
   cleanWind.put("barrier",text(wind,"barrier",50,true,"wind.barrier"));
   cleanWind.put("destination",text(wind,"destination",50,true,"wind.destination"));
   cleanWind.put("direction",text(wind,"direction",40,true,"wind.direction"));
   cleanWind.put("effect",text(wind,"effect",140,true,"wind.effect"));
   cleanWind.put("peak",text(wind,"peak",60,true,"wind.peak"));
   putIf(cleanWind,"caution",text(wind,"caution",160,false,"wind.caution"));
   out.put("kind","wind").put("wind",cleanWind);return out;
  }
  JSONArray days=array(in,"days","days",MAX_DAYS),cleanDays=new JSONArray();
  if(days.length()==0)throw new Invalid("days must not be empty");
  int mealTotal=0;
  for(int d=0;d<days.length();d++){
   String at="days["+d+"]";JSONObject day=object(days.opt(d),at),cleanDay=new JSONObject();
   cleanDay.put("date",match(text(day,"date",10,true,at+".date"),DATE,at+".date"));
   putIf(cleanDay,"label",text(day,"label",24,false,at+".label"));
   JSONArray meals=array(day,"meals",at+".meals",MAX_MEALS),cleanMeals=new JSONArray();
   mealTotal+=meals.length();if(mealTotal>MAX_MEALS)throw new Invalid("more than "+MAX_MEALS+" meals");
   for(int m=0;m<meals.length();m++){
    String mealAt=at+".meals["+m+"]";JSONObject meal=object(meals.opt(m),mealAt),cleanMeal=new JSONObject();
    putIf(cleanMeal,"id",match(text(meal,"id",64,false,mealAt+".id"),ID,mealAt+".id"));
    cleanMeal.put("name",text(meal,"name",60,true,mealAt+".name"));
    putIf(cleanMeal,"time",match(text(meal,"time",5,false,mealAt+".time"),TIME,mealAt+".time"));
    putIf(cleanMeal,"notes",text(meal,"notes",160,false,mealAt+".notes"));
    Object estimated=meal.opt("estimated");
    if(estimated!=null&&estimated!=JSONObject.NULL){if(!(estimated instanceof Boolean))throw new Invalid(mealAt+".estimated must be true or false");cleanMeal.put("estimated",estimated);}
    JSONArray nutrients=array(meal,"nutrients",mealAt+".nutrients",MAX_NUTRIENTS),cleanNutrients=new JSONArray();
    for(int n=0;n<nutrients.length();n++){
     String nutrientAt=mealAt+".nutrients["+n+"]";JSONObject nutrient=object(nutrients.opt(n),nutrientAt),cleanNutrient=new JSONObject();
     cleanNutrient.put("name",text(nutrient,"name",32,true,nutrientAt+".name"));
     Object value=nutrient.opt("value");
     if(value!=null&&value!=JSONObject.NULL){
      if(!(value instanceof Number))throw new Invalid(nutrientAt+".value must be a number");
      double number=((Number)value).doubleValue();
      if(Double.isNaN(number)||Double.isInfinite(number)||number<0||number>MAX_VALUE)throw new Invalid(nutrientAt+".value must be finite and not negative");
      cleanNutrient.put("value",value);
     }
     putIf(cleanNutrient,"unit",text(nutrient,"unit",12,false,nutrientAt+".unit"));
     String group=text(nutrient,"group",8,false,nutrientAt+".group");
     if(group!=null){if(!group.equals("macro")&&!group.equals("micro"))throw new Invalid(nutrientAt+".group must be macro or micro");cleanNutrient.put("group",group);}
     cleanNutrients.put(cleanNutrient);
    }
    cleanMeal.put("nutrients",cleanNutrients);cleanMeals.put(cleanMeal);
   }
   cleanDay.put("meals",cleanMeals);cleanDays.put(cleanDay);
  }
  out.put("days",cleanDays);return out;
 }

 private static void putIf(JSONObject target,String key,String value)throws JSONException{if(value!=null)target.put(key,value);}
 private static String match(String value,Pattern pattern,String at)throws Invalid{if(value!=null&&!pattern.matcher(value).matches())throw new Invalid(at+" has an unsupported format");return value;}
 private static JSONObject object(Object value,String at)throws Invalid{if(!(value instanceof JSONObject))throw new Invalid(at+" must be an object");return (JSONObject)value;}
 /** A missing optional list is empty; a present one must really be a list within the limit. */
 private static JSONArray array(JSONObject parent,String key,String at,int max)throws Invalid{
  Object value=parent.opt(key);if(value==null||value==JSONObject.NULL)return new JSONArray();
  if(!(value instanceof JSONArray))throw new Invalid(at+" must be a list");
  if(((JSONArray)value).length()>max)throw new Invalid(at+" has more than "+max+" entries");
  return (JSONArray)value;
 }
 private static String text(JSONObject parent,String key,int max,boolean required,String at)throws Invalid{
  Object value=parent.opt(key);
  if(value==null||value==JSONObject.NULL){if(required)throw new Invalid(at+" is required");return null;}
  if(!(value instanceof String))throw new Invalid(at+" must be text");
  String trimmed=((String)value).trim();
  if(trimmed.isEmpty()){if(required)throw new Invalid(at+" is required");return null;}
  if(trimmed.length()>max)throw new Invalid(at+" is longer than "+max+" characters");
  for(int i=0;i<trimmed.length();i++)if(Character.isISOControl(trimmed.charAt(i)))throw new Invalid(at+" contains control characters");
  return trimmed;
 }
}
