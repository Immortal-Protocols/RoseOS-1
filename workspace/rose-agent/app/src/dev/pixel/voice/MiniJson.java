package dev.pixel.voice;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Strict, bounded JSON reader for public update metadata (no Android types, so the update policy is testable).
 * Objects become LinkedHashMap, arrays ArrayList, integers Long, fractions Double, true/false Boolean and
 * null NULL. Duplicate keys, trailing data, depth over 32 and non-standard syntax are rejected. */
final class MiniJson {
 static final Object NULL=new Object(){public String toString(){return "null";}};
 private final String s;private int i;
 private MiniJson(String s){this.s=s;}
 static Object parse(String text){
  if(text==null)throw new IllegalArgumentException("empty");
  MiniJson p=new MiniJson(text);p.ws();Object v=p.value(0);p.ws();if(p.i!=p.s.length())throw new IllegalArgumentException("trailing data");return v;
 }
 private void ws(){while(i<s.length()&&" \t\r\n".indexOf(s.charAt(i))>=0)i++;}
 private char peek(){if(i>=s.length())throw new IllegalArgumentException("truncated");return s.charAt(i);}
 private Object value(int depth){
  if(depth>32)throw new IllegalArgumentException("too deep");
  char c=peek();
  if(c=='{'){i++;Map<String,Object> m=new LinkedHashMap<>();ws();if(peek()=='}'){i++;return m;}
   while(true){ws();if(peek()!='"')throw new IllegalArgumentException("key");String k=string();ws();expect(':');ws();
    if(m.containsKey(k))throw new IllegalArgumentException("duplicate key");m.put(k,value(depth+1));ws();
    if(peek()==','){i++;continue;}expect('}');return m;}}
  if(c=='['){i++;List<Object> a=new ArrayList<>();ws();if(peek()==']'){i++;return a;}
   while(true){ws();a.add(value(depth+1));ws();if(peek()==','){i++;continue;}expect(']');return a;}}
  if(c=='"')return string();
  if(s.startsWith("true",i)){i+=4;return Boolean.TRUE;}
  if(s.startsWith("false",i)){i+=5;return Boolean.FALSE;}
  if(s.startsWith("null",i)){i+=4;return NULL;}
  return number();
 }
 private void expect(char c){if(peek()!=c)throw new IllegalArgumentException("expected "+c);i++;}
 private String string(){
  expect('"');StringBuilder b=new StringBuilder();
  while(true){char c=peek();i++;
   if(c=='"')return b.toString();
   if(c<0x20)throw new IllegalArgumentException("control character");
   if(c!='\\'){b.append(c);continue;}
   char e=peek();i++;
   switch(e){case '"':b.append('"');break;case '\\':b.append('\\');break;case '/':b.append('/');break;case 'b':b.append('\b');break;
    case 'f':b.append('\f');break;case 'n':b.append('\n');break;case 'r':b.append('\r');break;case 't':b.append('\t');break;
    case 'u':if(i+4>s.length())throw new IllegalArgumentException("escape");b.append((char)Integer.parseInt(s.substring(i,i+4),16));i+=4;break;
    default:throw new IllegalArgumentException("escape");}
  }
 }
 private Object number(){
  int start=i;if(peek()=='-')i++;
  if(peek()=='0')i++;else{if(!Character.isDigit(peek()))throw new IllegalArgumentException("value");while(i<s.length()&&Character.isDigit(s.charAt(i)))i++;}
  boolean fraction=false;
  if(i<s.length()&&s.charAt(i)=='.'){fraction=true;i++;if(!Character.isDigit(peek()))throw new IllegalArgumentException("number");while(i<s.length()&&Character.isDigit(s.charAt(i)))i++;}
  if(i<s.length()&&(s.charAt(i)=='e'||s.charAt(i)=='E')){fraction=true;i++;if(peek()=='+'||peek()=='-')i++;if(!Character.isDigit(peek()))throw new IllegalArgumentException("number");while(i<s.length()&&Character.isDigit(s.charAt(i)))i++;}
  String n=s.substring(start,i);
  if(!fraction&&n.replace("-","").length()<=18)return Long.parseLong(n);
  return Double.parseDouble(n);
 }
}
