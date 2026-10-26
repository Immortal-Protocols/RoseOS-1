package dev.pixel.calculator.agent;
import java.math.*;import java.util.*;
/** Decimal arithmetic with multiplication/division precedence and repeat equals. */
public final class Calculator {
 public String entry="0",repeatOp="",repeatValue="";public boolean waiting=false,result=false,error=false;
 public final ArrayList<String> tokens=new ArrayList<>();
 public String completedExpression=null;
 private static final MathContext MC=new MathContext(16,RoundingMode.HALF_UP);
 public void press(String key){completedExpression=null;try{
  if(key.equals("AC")){reset();return;}
  if(key.equals("C")){entry="0";error=false;waiting=false;result=false;repeatOp="";return;}
  if(error)reset();
  if(key.matches("[0-9]")||key.equals(".")){if(waiting||result){if(result)tokens.clear();entry="0";waiting=result=false;repeatOp="";}
   if(key.equals(".")){if(!entry.contains("."))entry+=".";return;}
   if(entry.replace("-","").replace(".","").length()>=15)return;
   if(entry.equals("0"))entry=key;else if(entry.equals("-0"))entry="-"+key;else entry+=key;return;
  }
  if(key.equals("back")){if(waiting||result)return;entry=entry.length()>1?entry.substring(0,entry.length()-1):"0";if(entry.equals("-"))entry="0";return;}
  if(key.equals("±")){if(waiting){entry="0";waiting=false;}entry=entry.startsWith("-")?entry.substring(1):"-"+entry;result=false;repeatOp="";return;}
  if(key.equals("%")){BigDecimal n=number(entry).divide(new BigDecimal(100),MC);if(!tokens.isEmpty()){String op=tokens.get(tokens.size()-1);if(op.equals("+")||op.equals("−")){ArrayList<String> prefix=new ArrayList<>(tokens);prefix.remove(prefix.size()-1);n=evaluate(prefix).multiply(n,MC);}}entry=format(n);waiting=result=false;repeatOp="";return;}
  if(isOperator(key)){repeatOp="";if(waiting){tokens.set(tokens.size()-1,key);return;}if(result)tokens.clear();tokens.add(entry);tokens.add(key);waiting=true;result=false;return;}
  if(key.equals("=")){
   if(tokens.isEmpty()){if(!repeatOp.isEmpty()){String before=entry;entry=format(apply(number(entry),repeatOp,number(repeatValue)));completedExpression=before+" "+repeatOp+" "+repeatValue;}result=true;waiting=false;return;}
   ArrayList<String> expr=new ArrayList<>(tokens);expr.add(entry);repeatOp=tokens.get(tokens.size()-1);repeatValue=entry;String answer=format(evaluate(expr));completedExpression=String.join(" ",expr);entry=answer;tokens.clear();waiting=false;result=true;
  }
 }catch(ArithmeticException|NumberFormatException ex){entry="Error";tokens.clear();waiting=false;result=false;error=true;repeatOp="";}}
 public void reset(){entry="0";tokens.clear();waiting=result=error=false;repeatOp=repeatValue="";completedExpression=null;}
 public String expression(){return tokens.isEmpty()?"":String.join(" ",tokens);}
 public String activeOperator(){return waiting&&!tokens.isEmpty()?tokens.get(tokens.size()-1):"";}
 public void recall(String value){reset();number(value);entry=value;result=true;}
 private static boolean isOperator(String s){return s.equals("+")||s.equals("−")||s.equals("×")||s.equals("÷");}
 private static BigDecimal number(String s){return new BigDecimal(s);}
 private static String format(BigDecimal n){if(n.signum()==0)return "0";BigDecimal x=n.round(MC).stripTrailingZeros();if(x.precision()-x.scale()>15||x.scale()>14)return x.toEngineeringString();return x.toPlainString();}
 private static BigDecimal apply(BigDecimal a,String op,BigDecimal b){switch(op){case "+":return a.add(b,MC);case "−":return a.subtract(b,MC);case "×":return a.multiply(b,MC);case "÷":return a.divide(b,MC);default:throw new ArithmeticException();}}
 private static BigDecimal evaluate(List<String> list){BigDecimal sum=BigDecimal.ZERO,term=number(list.get(0));String add="+";for(int i=1;i<list.size();i+=2){String op=list.get(i);BigDecimal next=number(list.get(i+1));if(op.equals("×")||op.equals("÷"))term=apply(term,op,next);else{sum=apply(sum,add,term);add=op;term=next;}}return apply(sum,add,term);}
}
