package dev.pixel.calculator.agent;
import android.content.*;import org.json.*;import java.util.*;
/** One model and persistence path shared by the unchanged keypad and agent API. */
final class CalculatorSession {
 private static CalculatorSession instance;
 final Calculator calc=new Calculator();JSONArray history=new JSONArray();private final SharedPreferences prefs;long revision;
 static synchronized CalculatorSession get(Context c){if(instance==null)instance=new CalculatorSession(c.getApplicationContext());return instance;}
 private CalculatorSession(Context c){prefs=c.getSharedPreferences("calculator",0);try{
  JSONObject o=new JSONObject(prefs.getString("state","{}"));calc.entry=o.optString("entry","0");calc.waiting=o.optBoolean("waiting");calc.result=o.optBoolean("result");calc.error=o.optBoolean("error");calc.repeatOp=o.optString("repeatOp","");calc.repeatValue=o.optString("repeatValue","");
  JSONArray a=o.optJSONArray("tokens");if(a!=null)for(int i=0;i<a.length();i++)calc.tokens.add(a.getString(i));history=new JSONArray(prefs.getString("history","[]"));revision=prefs.getLong("revision",0);
 }catch(Exception e){calc.reset();history=new JSONArray();}}
 void press(String key){calc.press(key);record();save();}
 private void record(){if(calc.completedExpression==null)return;try{JSONObject item=new JSONObject().put("expression",calc.completedExpression).put("answer",calc.entry);JSONArray next=new JSONArray();next.put(item);for(int i=0;i<Math.min(history.length(),49);i++)next.put(history.get(i));history=next;}catch(JSONException e){throw new IllegalStateException(e);}}
 JSONObject evaluate(String expression,int equalsCount)throws Exception{
  List<String> keys=ExpressionKeys.parse(expression);if(equalsCount<1||equalsCount>10)throw new IllegalArgumentException("equalsCount must be 1..10");
  calc.reset();for(String key:keys)calc.press(key);for(int i=0;i<equalsCount;i++){calc.press("=");record();}save();return state().put("status",calc.error?"calculation_error":"verified").put("requestedExpression",expression).put("equalsCount",equalsCount);
 }
 JSONObject state()throws JSONException{return new JSONObject().put("status","ok").put("entry",calc.entry).put("pendingExpression",calc.expression()).put("result",calc.result).put("waitingForOperand",calc.waiting).put("repeatOperation",calc.repeatOp).put("repeatValue",calc.repeatValue).put("error",calc.error).put("revision",revision).put("latestHistory",history.length()>0?history.get(0):JSONObject.NULL);}
 void save(){try{JSONObject o=new JSONObject().put("entry",calc.entry).put("tokens",new JSONArray(calc.tokens)).put("waiting",calc.waiting).put("result",calc.result).put("waitingForOperand",calc.waiting).put("repeatOperation",calc.repeatOp).put("repeatValue",calc.repeatValue).put("error",calc.error).put("repeatOp",calc.repeatOp).put("repeatValue",calc.repeatValue);
  revision++;if(!prefs.edit().putString("state",o.toString()).putString("history",history.toString()).putLong("revision",revision).commit())throw new IllegalStateException("Calculation state was not saved");
 }catch(JSONException e){throw new IllegalStateException(e);}}
}
