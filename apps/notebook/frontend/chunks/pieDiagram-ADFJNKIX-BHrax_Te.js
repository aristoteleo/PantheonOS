import{a3 as w,Y as O,bm as _,_ as p,g as j,s as H,a as J,b as K,t as Q,q as U,e as F,c as X,E as Z,I as ee,P as te,b0 as ae,f as ne,z as ie,G as re}from"./appEntry-Bun0fdMM.js";import{p as le}from"./chunk-4BX2VUAB-BhlDlXVp.js";import{p as se}from"./treemap-KMMF4GRG-ZL8E6jmx.js";import{d as L}from"./arc-BPU56rfz.js";function oe(e,a){return a<e?-1:a>e?1:a>=e?0:NaN}function ce(e){return e}function ue(){var e=ce,a=oe,f=null,s=w(0),o=w(O),y=w(0);function l(t){var i,c=(t=_(t)).length,d,v,m=0,u=new Array(c),r=new Array(c),x=+s.apply(this,arguments),S=Math.min(O,Math.max(-O,o.apply(this,arguments)-x)),h,A=Math.min(Math.abs(S)/c,y.apply(this,arguments)),C=A*(S<0?-1:1),g;for(i=0;i<c;++i)(g=r[u[i]=i]=+e(t[i],i,t))>0&&(m+=g);for(a!=null?u.sort(function($,b){return a(r[$],r[b])}):f!=null&&u.sort(function($,b){return f(t[$],t[b])}),i=0,v=m?(S-c*C)/m:0;i<c;++i,x=h)d=u[i],g=r[d],h=x+(g>0?g*v:0)+C,r[d]={data:t[d],index:i,value:g,startAngle:x,endAngle:h,padAngle:A};return r}return l.value=function(t){return arguments.length?(e=typeof t=="function"?t:w(+t),l):e},l.sortValues=function(t){return arguments.length?(a=t,f=null,l):a},l.sort=function(t){return arguments.length?(f=t,a=null,l):f},l.startAngle=function(t){return arguments.length?(s=typeof t=="function"?t:w(+t),l):s},l.endAngle=function(t){return arguments.length?(o=typeof t=="function"?t:w(+t),l):o},l.padAngle=function(t){return arguments.length?(y=typeof t=="function"?t:w(+t),l):y},l}var pe=re.pie,R={sections:new Map,showData:!1},D=R.sections,W=R.showData,de=structuredClone(pe),ge=p(()=>structuredClone(de),"getConfig"),fe=p(()=>{D=new Map,W=R.showData,ie()},"clear"),he=p(({label:e,value:a})=>{if(a<0)throw new Error(`"${e}" has invalid value: ${a}. Negative values are not allowed in pie charts. All slice values must be >= 0.`);D.has(e)||(D.set(e,a),F.debug(`added new section: ${e}, with value: ${a}`))},"addSection"),me=p(()=>D,"getSections"),xe=p(e=>{W=e},"setShowData"),we=p(()=>W,"getShowData"),V={getConfig:ge,clear:fe,setDiagramTitle:U,getDiagramTitle:Q,setAccTitle:K,getAccTitle:J,setAccDescription:H,getAccDescription:j,addSection:he,getSections:me,setShowData:xe,getShowData:we},ye=p((e,a)=>{le(e,a),a.setShowData(e.showData),e.sections.map(a.addSection)},"populateDb"),ve={parse:p(async e=>{const a=await se("pie",e);F.debug(a),ye(a,V)},"parse")},Se=p(e=>`
  .pieCircle{
    stroke: ${e.pieStrokeColor};
    stroke-width : ${e.pieStrokeWidth};
    opacity : ${e.pieOpacity};
  }
  .pieOuterCircle{
    stroke: ${e.pieOuterStrokeColor};
    stroke-width: ${e.pieOuterStrokeWidth};
    fill: none;
  }
  .pieTitleText {
    text-anchor: middle;
    font-size: ${e.pieTitleTextSize};
    fill: ${e.pieTitleTextColor};
    font-family: ${e.fontFamily};
  }
  .slice {
    font-family: ${e.fontFamily};
    fill: ${e.pieSectionTextColor};
    font-size:${e.pieSectionTextSize};
    // fill: white;
  }
  .legend text {
    fill: ${e.pieLegendTextColor};
    font-family: ${e.fontFamily};
    font-size: ${e.pieLegendTextSize};
  }
`,"getStyles"),$e=Se,be=p(e=>{const a=[...e.values()].reduce((s,o)=>s+o,0),f=[...e.entries()].map(([s,o])=>({label:s,value:o})).filter(s=>s.value/a*100>=1).sort((s,o)=>o.value-s.value);return ue().value(s=>s.value)(f)},"createPieArcs"),Ae=p((e,a,f,s)=>{F.debug(`rendering pie chart
`+e);const o=s.db,y=X(),l=Z(o.getConfig(),y.pie),t=40,i=18,c=4,d=450,v=d,m=ee(a),u=m.append("g");u.attr("transform","translate("+v/2+","+d/2+")");const{themeVariables:r}=y;let[x]=te(r.pieOuterStrokeWidth);x??=2;const S=l.textPosition,h=Math.min(v,d)/2-t,A=L().innerRadius(0).outerRadius(h),C=L().innerRadius(h*S).outerRadius(h*S);u.append("circle").attr("cx",0).attr("cy",0).attr("r",h+x/2).attr("class","pieOuterCircle");const g=o.getSections(),$=be(g),b=[r.pie1,r.pie2,r.pie3,r.pie4,r.pie5,r.pie6,r.pie7,r.pie8,r.pie9,r.pie10,r.pie11,r.pie12];let T=0;g.forEach(n=>{T+=n});const E=$.filter(n=>(n.data.value/T*100).toFixed(0)!=="0"),k=ae(b);u.selectAll("mySlices").data(E).enter().append("path").attr("d",A).attr("fill",n=>k(n.data.label)).attr("class","pieCircle"),u.selectAll("mySlices").data(E).enter().append("text").text(n=>(n.data.value/T*100).toFixed(0)+"%").attr("transform",n=>"translate("+C.centroid(n)+")").style("text-anchor","middle").attr("class","slice"),u.append("text").text(o.getDiagramTitle()).attr("x",0).attr("y",-400/2).attr("class","pieTitleText");const N=[...g.entries()].map(([n,z])=>({label:n,value:z})),M=u.selectAll(".legend").data(N).enter().append("g").attr("class","legend").attr("transform",(n,z)=>{const B=i+c,G=B*N.length/2,I=12*i,Y=z*B-G;return"translate("+I+","+Y+")"});M.append("rect").attr("width",i).attr("height",i).style("fill",n=>k(n.label)).style("stroke",n=>k(n.label)),M.append("text").attr("x",i+c).attr("y",i-c).text(n=>o.getShowData()?`${n.label} [${n.value}]`:n.label);const q=Math.max(...M.selectAll("text").nodes().map(n=>n?.getBoundingClientRect().width??0)),P=v+t+i+c+q;m.attr("viewBox",`0 0 ${P} ${d}`),ne(m,d,P,l.useMaxWidth)},"draw"),Ce={draw:Ae},De={parser:ve,db:V,renderer:Ce,styles:$e};export{De as diagram};
