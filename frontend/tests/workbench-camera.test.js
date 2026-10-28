import test from 'node:test';
import assert from 'node:assert/strict';
import { homeCamera, zoomCamera, wheelZoom, dragCamera, SceneDrag, frameBounds, MIN_ZOOM, MAX_ZOOM } from '../src/workbench-camera.js';

test('wheel keeps the same optical point under the pointer after a pan',()=>{
  const camera={zoom:175,x:63,y:-42}, anchor={x:127,y:81};
  const next=wheelZoom(camera,-90,0,400,anchor);
  assert.ok(next.zoom>camera.zoom);
  assert.ok(Math.abs((anchor.x-camera.x)/camera.zoom-(anchor.x-next.x)/next.zoom)<1e-12);
  assert.ok(Math.abs((anchor.y-camera.y)/camera.zoom-(anchor.y-next.y)/next.zoom)<1e-12);
});

test('pixel, line and page devices preserve fine input and clamp extremes',()=>{
  const c=homeCamera(), anchor={x:40,y:25};
  assert.deepEqual(wheelZoom(c,16,0,400,anchor),wheelZoom(c,1,1,400,anchor));
  assert.deepEqual(wheelZoom(c,200,0,200,anchor),wheelZoom(c,1,2,200,anchor));
  assert.notEqual(wheelZoom(c,.1,0,400,anchor).zoom,c.zoom);
  let camera=c;
  for(let i=0;i<1000;i++) camera=wheelZoom(camera,-1e300,0,400,anchor);
  assert.equal(camera.zoom,MAX_ZOOM);
  assert.ok(Object.values(camera).every(Number.isFinite));
  for(let i=0;i<1000;i++) camera=wheelZoom(camera,1e300,0,400,anchor);
  assert.equal(camera.zoom,MIN_ZOOM);
  for(const bad of [NaN,Infinity,-Infinity]) assert.equal(wheelZoom(c,bad,0,400,anchor),c);
});

test('drag and centre zoom preserve pan semantics; home resets both without touching selection',()=>{
  const c=dragCamera(homeCamera(),130,-75);
  assert.deepEqual(c,{zoom:100,x:130,y:-75});
  assert.deepEqual(zoomCamera(c,200),{zoom:200,x:260,y:-150});
  assert.deepEqual(homeCamera(),{zoom:100,x:0,y:0});
  assert.deepEqual(c,{zoom:100,x:130,y:-75});
});

test('small click selects, drag returning to start never selects, and pointer cancel releases a click',()=>{
  const drag=new SceneDrag();
  drag.begin(1,10,20,homeCamera(),3);
  assert.equal(drag.move(1,12,21),null);
  assert.deepEqual(drag.end(1,12,21),{surface:3});
  drag.begin(1,10,20,homeCamera(),3);
  assert.deepEqual(drag.move(1,30,40),{zoom:100,x:20,y:20});
  drag.move(1,10,20);
  assert.deepEqual(drag.end(1,10,20),{surface:null});
  drag.begin(1,10,20,homeCamera(),3);
  assert.deepEqual(drag.end(1,10,20,true),{surface:null});
  assert.equal(drag.start,null);
});

test('captured drag ignores other pointers, ends outside canvas and detects movement at release',()=>{
  const drag=new SceneDrag();drag.begin(1,10,20,homeCamera(),3);
  assert.equal(drag.begin(2,0,0,homeCamera(),4),false);
  assert.equal(drag.move(2,600,900),null);
  assert.equal(drag.end(2,0,0,true),null);
  assert.deepEqual(drag.move(1,600,900),{zoom:100,x:590,y:880});
  assert.deepEqual(drag.end(1,600,900),{surface:null});
  assert.equal(drag.move(1,10,20),null);
  drag.begin(3,0,0,homeCamera(),4);
  assert.deepEqual(drag.end(3,5,0),{surface:null});
});

test('new rays cannot reframe a moved camera; home fits the latest union and missing content retains frame',()=>{
  const contour=[0,-5,50,5],newRays=[-25,-40,100,40];
  const camera=dragCamera(zoomCamera(homeCamera(),200),20,30);
  assert.equal(frameBounds(contour,newRays,camera),contour);
  assert.equal(frameBounds(contour,null,camera),contour);
  assert.equal(frameBounds(contour,newRays,homeCamera()),newRays);
  assert.equal(frameBounds(null,newRays,camera),newRays);
});
