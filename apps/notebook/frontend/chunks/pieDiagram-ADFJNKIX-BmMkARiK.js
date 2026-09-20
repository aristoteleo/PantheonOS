import{S as w,W as F,aT as j,_ as u,g as G,s as J,a as K,b as Q,q as U,p as X,l as O,c as Y,D as Z,H as ee,a5 as te,aI as ae,e as ne,y as ie,F as re}from"./UnifiedMarkdownEditor-rlSmthWR.js";import{p as le}from"./chunk-4BX2VUAB-D4qOcGxF.js";import{p as se}from"./treemap-KMMF4GRG-Cm17mmI2.js";import{d as P}from"./arc-Cz6RnQ4T.js";function oe(e,a){return a<e?-1:a>e?1:a>=e?0:NaN}function ce(e){return e}function pe(){var e=ce,a=oe,f=null,s=w(0),o=w(F),y=w(0);function l(t){var i,c=(t=j(t)).length,d,v,m=0,p=new Array(c),r=new Array(c),x=+s.apply(this,arguments),S=Math.min(F,Math.max(-F,o.apply(this,arguments)-x)),h,b=Math.min(Math.abs(S)/c,y.apply(this,arguments)),D=b*(S<0?-1:1),g;for(i=0;i<c;++i)(g=r[p[i]=i]=+e(t[i],i,t))>0&&(m+=g);for(a!=null?p.sort(function($,A){return a(r[$],r[A])}):f!=null&&p.sort(function($,A){return f(t[$],t[A])}),i=0,v=m?(S-c*D)/m:0;i<c;++i,x=h)d=p[i],g=r[d],h=x+(g>0?g*v:0)+D,r[d]={data:t[d],index:i,value:g,startAngle:x,endAngle:h,padAngle:b};return r}return l.value=function(t){return arguments.length?(e=typeof t=="function"?t:w(+t),l):e},l.sortValues=function(t){return arguments.length?(a=t,f=null,l):a},l.sort=function(t){return arguments.length?(f=t,a=null,l):f},l.startAngle=function(t){return arguments.length?(s=typeof t=="function"?t:w(+t),l):s},l.endAngle=function(t){return arguments.length?(o=typeof t=="function"?t:w(+t),l):o},l.padAngle=function(t){return arguments.length?(y=typeof t=="function"?t:w(+t),l):y},l}var ue=re.pie,R={sections:new Map,showData:!1},T=R.sections,W=R.showData,de=structuredClone(ue),ge=u(()=>structuredClone(de),"getConfig"),fe=u(()=>{T=new Map,W=R.showData,ie()},"clear"),he=u(({label:e,value:a})=>{if(a<0)throw new Error(`"${e}" has invalid value: ${a}. Negative values are not allowed in pie charts. All slice values must be >= 0.`);T.has(e)||(T.set(e,a),O.debug(`added new section: ${e}, with value: ${a}`))},"addSection"),me=u(()=>T,"getSections"),xe=u(e=>{W=e},"setShowData"),we=u(()=>W,"getShowData"),V={getConfig:ge,clear:fe,setDiagramTitle:X,getDiagramTitle:U,setAccTitle:Q,getAccTitle:K,setAccDescription:J,getAccDescription:G,addSection:he,getSections:me,setShowData:xe,getShowData:we},ye=u((e,a)=>{le(e,a),a.setShowData(e.showData),e.sections.map(a.addSection)},"populateDb"),ve={parse:u(async e=>{const a=await se("pie",e);O.debug(a),ye(a,V)},"parse")},Se=u(e=>`
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
`,"getStyles"),$e=Se,Ae=u(e=>{const a=[...e.values()].reduce((s,o)=>s+o,0),f=[...e.entries()].map(([s,o])=>({label:s,value:o})).filter(s=>s.value/a*100>=1).sort((s,o)=>o.value-s.value);return pe().value(s=>s.value)(f)},"createPieArcs"),be=u((e,a,f,s)=>{O.debug(`rendering pie chart
`+e);const o=s.db,y=Y(),l=Z(o.getConfig(),y.pie),t=40,i=18,c=4,d=450,v=d,m=ee(a),p=m.append("g");p.attr("transform","translate("+v/2+","+d/2+")");const{themeVariables:r}=y;let[x]=te(r.pieOuterStrokeWidth);x??=2;const S=l.textPosition,h=Math.min(v,d)/2-t,b=P().innerRadius(0).outerRadius(h),D=P().innerRadius(h*S).outerRadius(h*S);p.append("circle").attr("cx",0).attr("cy",0).attr("r",h+x/2).attr("class","pieOuterCircle");const g=o.getSections(),$=Ae(g),A=[r.pie1,r.pie2,r.pie3,r.pie4,r.pie5,r.pie6,r.pie7,r.pie8,r.pie9,r.pie10,r.pie11,r.pie12];let C=0;g.forEach(n=>{C+=n});const N=$.filter(n=>(n.data.value/C*100).toFixed(0)!=="0"),k=ae(A);p.selectAll("mySlices").data(N).enter().append("path").attr("d",b).attr("fill",n=>k(n.data.label)).attr("class","pieCircle"),p.selectAll("mySlices").data(N).enter().append("text").text(n=>(n.data.value/C*100).toFixed(0)+"%").attr("transform",n=>"translate("+D.centroid(n)+")").style("text-anchor","middle").attr("class","slice"),p.append("text").text(o.getDiagramTitle()).attr("x",0).attr("y",-400/2).attr("class","pieTitleText");const B=[...g.entries()].map(([n,z])=>({label:n,value:z})),M=p.selectAll(".legend").data(B).enter().append("g").attr("class","legend").attr("transform",(n,z)=>{const L=i+c,H=L*B.length/2,I=12*i,_=z*L-H;return"translate("+I+","+_+")"});M.append("rect").attr("width",i).attr("height",i).style("fill",n=>k(n.label)).style("stroke",n=>k(n.label)),M.append("text").attr("x",i+c).attr("y",i-c).text(n=>o.getShowData()?`${n.label} [${n.value}]`:n.label);const q=Math.max(...M.selectAll("text").nodes().map(n=>n?.getBoundingClientRect().width??0)),E=v+t+i+c+q;m.attr("viewBox",`0 0 ${E} ${d}`),ne(m,d,E,l.useMaxWidth)},"draw"),De={draw:be},Te={parser:ve,db:V,renderer:De,styles:$e};export{Te as diagram};
