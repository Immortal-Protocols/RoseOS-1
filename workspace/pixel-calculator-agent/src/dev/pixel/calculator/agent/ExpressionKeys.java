package dev.pixel.calculator.agent;
import java.util.*;
import java.util.regex.*;
/** Parses only expressions supported by the existing keypad; never evaluates arithmetic. */
public final class ExpressionKeys {
 public static List<String> parse(String expression){
  if(expression==null||expression.length()>256)throw new IllegalArgumentException("Expression must be at most 256 characters");
  String text=expression.replaceAll("\\s+","").replace('−','-').replace('×','*').replace('÷','/');
  ArrayList<String> keys=new ArrayList<>();int at=0;
  Pattern operand=Pattern.compile("([+-]?)([0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)(%?)");
  while(at<text.length()){
   Matcher m=operand.matcher(text);m.region(at,text.length());if(!m.lookingAt())throw new IllegalArgumentException("Expected a number at position "+at);
   String digits=m.group(2);if(digits.replace(".","").length()>15)throw new IllegalArgumentException("The keypad supports at most 15 entered digits");
   for(int i=0;i<digits.length();i++)keys.add(digits.substring(i,i+1));
   if(m.group(1).equals("-"))keys.add("±");if(!m.group(3).isEmpty())keys.add("%");at=m.end();
   if(at==text.length())break;
   char op=text.charAt(at++);switch(op){case '+':keys.add("+");break;case '-':keys.add("−");break;case '*':keys.add("×");break;case '/':keys.add("÷");break;default:throw new IllegalArgumentException("Unsupported operator");}
   if(at==text.length())throw new IllegalArgumentException("Expression ends with an operator");
  }
  if(keys.isEmpty())throw new IllegalArgumentException("Expression is empty");return keys;
 }
}
