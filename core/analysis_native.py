"""Bounded ZOS-API image-quality extraction. Called only by the owner thread.

2024 R1 public IAR_DataSeries labels and exported units are checked at runtime;
unrecognized/localized contracts fail explicitly instead of guessing columns.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import math
import json
from pathlib import Path
import re
import tempfile
import time

from core.analysis_data import (AnalysisError, axis_identity, fail, finite, length_to_um,
                                primary_wave, settings_for, suite_verdict, wavefront_grid,
                                native_wavefront_statistics, compare_wavefront_statistics, MAX_RESULT_BYTES)
from core.analysis_runner import run_analysis, close_analysis, NativeOwnershipError, quarantine


def utc():
    return datetime.now(timezone.utc).isoformat()


def implementation(value):
    return getattr(value, "__implementation__", value)


def assign(settings, **values):
    for key, value in values.items():
        setattr(settings, key, value)
        if str(getattr(settings, key)) != str(value):
            fail(f"原生设置 {key} 未生效")


def enum_for(settings, property_name, name):
    import System
    prop = settings.GetType().GetProperty(property_name)
    return System.Enum.Parse(prop.PropertyType, name)


def columns(series):
    data = series.YData.Data
    if int(data.Rank) != 2:
        fail("原生曲线不是二维列数组")
    n, count = int(data.GetLength(0)), int(data.GetLength(1))
    if not 1 <= n <= 4096 or not 1 <= count <= 12:
        fail("原生曲线超出数据预算", "ANALYSIS_BUDGET_EXCEEDED")
    labels = [str(x) for x in series.SeriesLabels]
    x = [finite(v) for v in series.XData.Data]
    if len(x) != n or len(labels) != count:
        fail("原生曲线的标签/坐标/列数不匹配")
    y = [[float(data[i,j]) if math.isfinite(float(data[i,j])) else None for i in range(n)] for j in range(count)]
    if not any(v is not None for row in y for v in row):
        fail("原生曲线没有有效数据", "ANALYSIS_NO_DATA")
    return x, labels, y


def header(result):
    return [str(v) for v in result.HeaderData.Lines]


def exported_text(result):
    # Only a privately owned temporary result file; never a user supplied path.
    with tempfile.TemporaryDirectory(prefix="optiforge-analysis-") as folder:
        path = Path(folder)/"result.txt"
        result.GetTextFile(str(path))
        if not path.exists() or path.stat().st_size > 8*1024*1024:
            fail("原生文本导出失败或过大")
        raw = path.read_bytes()
        try:
            return raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")
        except UnicodeError:
            fail("不能确认原生结果文本编码")


def native_unit(text, kind):
    text = text.lower().replace("μ", "µ")
    if kind == "frequency" and re.search(r"(?:cycles?|周期|lp)\s*/\s*mm", text):
        return "lp/mm"
    if kind == "transverse" and re.search(r"(?:units?|单位)[^\n]*µm", text):
        return "µm"
    if kind == "opd" and (re.search(r"rms[^\n]*(?:waves?|波)",text)):
        return "waves"
    if kind == "curvature":
        for label, pattern in (("mm",r"(?:millimeters?|毫米)"), ("cm",r"(?:centimeters?|厘米)"),
                               ("in",r"(?:inches|inch|英寸)"), ("m",r"(?:meters?|米)")):
            if re.search(r"(?:displacement units?|位移单位)[^\n]*"+pattern, text):
                if re.search(r"(?:distortion units?|畸变单位)[^\n]*(?:percent|百分比)",text):
                    return label
    fail(f"未核实 {kind} 单位，停止解释该数据")


class NativeSuite:
    def __init__(self, session, model, settings, progress=None):
        self.session, self.system, self.zos = session, session.system, session.ZOSAPI
        self.model, self.settings = model, settings
        self.progress = progress or (lambda *args: None)
        self.deadline = time.monotonic() + settings["suite_timeout_s"]

    def remaining(self):
        value = min(self.settings["item_timeout_s"], self.item_deadline-time.monotonic(), self.deadline-time.monotonic())
        if value <= 0:
            raise AnalysisError("ANALYSIS_TIMEOUT", "分析已超出计算预算")
        return value

    @contextmanager
    def analysis(self, factory):
        self.remaining()
        analysis = getattr(self.system.Analyses,factory)()
        try:
            yield analysis, implementation(analysis.GetSettings())
        finally:
            close_analysis(analysis)

    def run(self, analysis):
        result = run_analysis(analysis, self.remaining())
        if int(result.NumberOfMessages):
            messages = [str(m.Text) for m in result.Messages]
            fail("原生分析返回消息："+"; ".join(messages), "ANALYSIS_NATIVE_MESSAGE")
        return result

    def mtf(self):
        curves, effective = [], []
        for field in self.settings["fields"]:
            self.progress("mtf",field)
            with self.analysis("New_FftMtf") as (a,s):
                s.Field.SetFieldNumber(field); s.Wavelength.UseAllWavelengths(); s.Surface.UseImageSurface()
                assign(s, Type=enum_for(s,"Type","Modulation"), MaximumFrequency=self.settings["max_frequency"],
                       SampleSize=getattr(self.zos.Analysis.SampleSizes,"S_"+self.settings["sample_size"]),
                       ShowDiffractionLimit=False,UsePolarization=False)
                res = self.run(a)
                unit = native_unit("\n".join(header(res)),"frequency")
                if int(res.NumberOfDataSeries) != 1:
                    fail("单视场 MTF 未返回唯一视场序列")
                series = res.GetDataSeries(0)
                x, labels, ys = columns(series)
                ids = [axis_identity(label) for label in labels]
                if len(ids)!=2 or set(ids)!={"tangential","sagittal"}:
                    fail("MTF 缺少可确认的 T/S 标签；不复制单列")
                for identity,label,y in zip(ids,labels,ys):
                    if not all(v is None or -.000001 <= v <= 1.000001 for v in y):
                        fail("MTF 归一化值超出有效范围")
                    curves.append({"field_index":field,"kind":identity,"label":label,"x":x,"y":y,
                                   "x_unit":unit,"y_unit":"normalized","description":str(series.Description)})
                effective.append({"field_index":field,"sample_size":str(s.SampleSize),"max_frequency":float(s.MaximumFrequency),
                                  "wavelengths":"all native system wavelengths and weights","polarization":False,
                                  "type":str(s.Type),"surface":"image"})
        return {"curves":curves,"effective_settings":effective,"coverage":{"fields":self.settings["fields"],"wavelengths":"all"}}

    def wavefront(self):
        wave = primary_wave(self.model)
        fields = []
        for field in self.settings["fields"]:
            self.progress("wavefront",field)
            with self.analysis("New_WavefrontMap") as (a,s):
                s.Field.SetFieldNumber(field);s.Wavelength.SetWavelengthNumber(wave["index"]);s.Surface.UseImageSurface()
                assign(s, Sampling=getattr(self.zos.Analysis.SampleSizes,f'S_{self.settings["wavefront_size"]}x{self.settings["wavefront_size"]}'),
                       RemoveTilt=False,ReferenceToPrimary=False,UseExitPupil=True,Subaperture_X=0.,Subaperture_Y=0.,Subaperture_R=1.,
                       Polarization=enum_for(s,"Polarization","None"),Rotation=enum_for(s,"Rotation","Rotate_0"))
                res = self.run(a)
                unit = native_unit("\n".join(header(res)),"opd")
                if int(res.NumberOfDataGrids)!=1:
                    fail("波前没有唯一有效网格", "ANALYSIS_NO_DATA")
                g = res.GetDataGrid(0); values = g.Values
                nx,ny = int(g.Nx),int(g.Ny)
                if nx!=values.GetLength(1) or ny!=values.GetLength(0) or nx>256 or ny>256:
                    fail("OPD 网格方向/预算不匹配")
                grid = wavefront_grid([[float(values[i,j]) for j in range(nx)] for i in range(ny)])
                grid.update(nx=nx,ny=ny,min_x=finite(g.MinX),min_y=finite(g.MinY),dx=finite(g.Dx),dy=finite(g.Dy),
                            unit=unit,coordinates="native grid row/column indices; API Min/Dx metadata retained, pupil coordinate unit unverified",
                            orientation="native IAR_DataGrid [row,column]; displayed with increasing row upward")
                native = native_wavefront_statistics(header(res))
                fields.append({"field_index":field,"wavelength_index":wave["index"],"wavelength_um":wave["wavelength_um"],
                               "grid":grid,"native_header":header(res),"native_statistics":native,
                               "statistics_comparison":compare_wavefront_statistics(grid,native),"effective_settings":{
                                   "sampling":str(s.Sampling),"remove_tilt":bool(s.RemoveTilt),"reference_to_primary":bool(s.ReferenceToPrimary),
                                   "use_exit_pupil":bool(s.UseExitPupil),"polarization":str(s.Polarization),"surface":"image"}})
        return {"fields":fields,"coverage":{"fields":self.settings["fields"],"wavelengths":[wave["index"]]}}

    def ray_fan(self):
        curves=[]
        for field in self.settings["fields"]:
            self.progress("ray_fan",field)
            with self.analysis("New_RayFan") as (a,s):
                s.Field.SetFieldNumber(field);s.Wavelength.UseAllWavelengths();s.Surface.UseImageSurface()
                assign(s,NumberOfRays=20,CheckApertures=True,VignettedPupil=True,
                       Tangential=enum_for(s,"Tangential","Aberration_Y"),Sagittal=enum_for(s,"Sagittal","Aberration_X"))
                res=self.run(a);unit=native_unit("\n".join(header(res)),"transverse")
                if int(res.NumberOfDataSeries)!=2:
                    fail("单视场 RayFan 不含两组可确认的光扇")
                found=[]
                for i in range(int(res.NumberOfDataSeries)):
                    series=res.GetDataSeries(i);desc=str(series.Description);low=desc.lower()
                    kind="tangential" if "tangential" in low or "子午" in desc else "sagittal" if "sagittal" in low or "弧矢" in desc else None
                    if kind is None:fail("不能根据原生描述确认光扇方向")
                    found.append(kind)
                    x,labels,ys=columns(series)
                    for label,y in zip(labels,ys):
                        try:wave=finite(label)
                        except (ValueError,AnalysisError):fail("RayFan 列标签不是可确认的波长")
                        matches=[w for w in self.model["wavelengths"] if abs(w["wavelength_um"]-wave)<=.00000051]
                        if len(matches)!=1:fail("RayFan 波长标签不能唯一匹配系统表")
                        curves.append({"field_index":field,"wavelength_index":matches[0]["index"],"wavelength_um":wave,
                                       "kind":kind,"x":x,"y":y,"x_unit":"normalized pupil", "y_unit":unit,
                                       "coordinates":"Ey(Py)" if kind=="tangential" else "Ex(Px)","description":desc})
                if set(found)!={"tangential","sagittal"}:fail("RayFan 方向标签重复或缺失")
        return {"curves":curves,"effective_settings":{"number_of_rays":20,"check_apertures":True,"vignetted_pupil":True,
                  "reference":"native chief ray; image local coordinates"},"coverage":{"fields":self.settings["fields"],"wavelengths":"all"}}

    def field_curvature(self):
        with self.analysis("New_FieldCurvatureAndDistortion") as (a,s):
            s.Wavelength.UseAllWavelengths()
            assign(s,DisplayAs=enum_for(s,"DisplayAs","Percent"),Distortion=enum_for(s,"Distortion","F_TanTheta"),
                   ScanType=enum_for(s,"ScanType","Plus_Y"),IgnoreVignette=False,MaximumCurvature=0.,MaximumDistortion=0.,FieldAspectRatio=1.)
            s.ReferenceField.SetFieldNumber(1)
            res=self.run(a)
            text=exported_text(res)
            unit=native_unit(text,"curvature");factor=length_to_um(unit)/1000
            curves=[]
            if not 1<=int(res.NumberOfDataSeries)<=12:fail("场曲没有有效波长序列", "ANALYSIS_NO_DATA")
            for i in range(int(res.NumberOfDataSeries)):
                series=res.GetDataSeries(i);x,labels,ys=columns(series)
                x_label=str(series.XLabel)
                if "度" in x_label or "deg" in x_label.lower():x_unit="deg"
                elif "height" in x_label.lower() or "高度" in x_label:x_unit=self.model["units"]
                else:fail("场曲扫描横坐标单位未知")
                ids=[axis_identity(label) for label in labels]
                if any(ids.count(kind)!=1 for kind in ("tangential","sagittal","distortion")):
                    fail("场曲/畸变缺少可识别列标签，不采用固定列号")
                desc=str(series.Description)
                match=re.search(r"([0-9]+\.[0-9]+)\s*[µμ]m",desc)
                if not match:fail("场曲波长来源未核实")
                for kind,label,y in zip(ids,labels,ys):
                    if kind is None:continue
                    curves.append({"kind":kind,"label":label,"wavelength_um":float(match[1]),"x":x,
                                   "y":[v*factor if v is not None and kind!="distortion" else v for v in y],
                                   "x_unit":x_unit,"y_unit":"%" if kind=="distortion" else "mm","description":desc})
        return {"curves":curves,"effective_settings":{"scan":"+Y continuous native scan, not selected field subset",
                "distortion":"F-Tan(theta)","reference_field":1,"ignore_vignette":False,"native_length_unit":unit},
                "coverage":{"fields":"continuous +Y to native maximum field","wavelengths":"all"}}

    def spot(self):
        from core.workbench_trace import normalize_fields, records
        from System import Double, Int32
        fields=normalize_fields(self.model.get("trace_fields",self.model["fields"]),self.model["settings"]["field_normalization"])
        if any(f.get("ignore") or not f.get("is_active",True) for f in fields):
            fail("忽略视场的原生归一化未验证")
        rings=self.settings["spot_rings"]
        pupil=[(0.,0.)]
        for ring in range(1,rings+1):
            for i in range(6*ring):
                angle=2*math.pi*i/(6*ring);radius=ring/rings
                pupil.append((radius*math.cos(angle),radius*math.sin(angle)))
        waves=self.model["wavelengths"];primary=primary_wave(self.model)
        requests=[]; selected=[f for f in fields if f["index"] in self.settings["fields"]]
        for f in selected:
            requests.append((f,primary,0.,0.,True))
            requests.extend((f,w,px,py,False) for w in waves for px,py in pupil)
        factor=length_to_um(self.model["units"])
        tool=self.session.open_tool("OpenBatchRayTrace")
        try:
            data=tool.CreateNormUnpol(len(requests),self.zos.Tools.RayTrace.RaysType.Real,len(self.model["surfaces"])-1)
            for f,w,px,py,_ in requests:
                data.AddRay(w["index"],f["hx"],f["hy"],px,py,getattr(self.zos.Tools.RayTrace.OPDMode,"None"))
            tool.RunAndWaitWithTimeout(self.remaining())
            if tool.IsRunning:
                raise AnalysisError("ANALYSIS_TIMEOUT","原生点列追迹超时")
            if not tool.Succeeded:fail(str(tool.ErrorMessage),"ANALYSIS_NATIVE_FAILED")
            data.StartReadingResults();raw=[]
            for _ in range(len(requests)+1):
                r=tuple(data.ReadNextResult(*([Int32(0)]*3+[Double(0)]*11)))
                if not r[0]:break
                raw.append(r)
            rows=records(raw,len(requests))
        finally:
            try:
                if tool.IsRunning:
                    if tool.CanCancel:tool.Cancel()
                    tool.WaitWithTimeout(2.)
                if tool.IsRunning:raise RuntimeError("点列工具仍在运行")
                if tool.Close() is False:raise RuntimeError("Close 返回 false")
            except Exception as error:
                raise quarantine(f"点列工具未安全释放：{error}") from error
        output={};chief={}
        for index,(f,w,px,py,is_chief) in enumerate(requests,1):
            r=rows[index];err,vig=int(r[2]),int(r[3]);valid=not err and not vig
            if is_chief:
                if not valid:fail(f'F{f["index"]} 主光线失败/截光，不能定义点列参考',"ANALYSIS_NO_DATA")
                chief[f["index"]]=[finite(r[4]),finite(r[5])]
                output[f["index"]]={"field_index":f["index"],"reference_xy_native":chief[f["index"]],"series":[],
                                     "counts":{"valid":0,"vignetted":0,"failed":0,"total":0}}
                continue
            item=output[f["index"]];counts=item["counts"];counts["total"]+=1
            counts["vignetted"]+=int(bool(vig));counts["failed"]+=int(bool(err))
            if not valid:continue
            xy=[(finite(r[4])-chief[f["index"]][0])*factor,(finite(r[5])-chief[f["index"]][1])*factor]
            counts["valid"]+=1
            series=next((s for s in item["series"] if s["wavelength_index"]==w["index"]),None)
            if series is None:
                series={"wavelength_index":w["index"],"wavelength_um":w["wavelength_um"],"weight":w.get("weight",1.),"points":[]}
                item["series"].append(series)
            series["points"].append(xy)
        for f in output.values():
            points=[(p,s["weight"]) for s in f["series"] for p in s["points"] if s["weight"]>0]
            if not points:fail("点列没有有效落点", "ANALYSIS_NO_DATA")
            total=math.fsum(w for p,w in points)
            f["rms_radius_um"]=math.sqrt(math.fsum(w*(p[0]**2+p[1]**2) for p,w in points)/total)
            f["geo_radius_um"]=max(math.hypot(*p) for p,w in points)
        return {"fields":list(output.values()),"unit":"µm","effective_settings":{"reference":"primary wavelength chief ray",
                "sampling":"hexapolar", "rings":rings,"rays_per_wavelength":len(pupil),"surface":"image","delta_focus":0,
                "statistics":"wavelength weighted, valid rays only; same batch sampling as plot; diagnostic INFO",
                "native_length_unit":self.model["units"]},"coverage":{"fields":self.settings["fields"],"wavelengths":"all"}}

    def execute(self):
        started=utc();clock=time.monotonic();items={}
        if str(self.system.Mode).split(".")[-1]!="Sequential":
            fail("当前只支持序列像质分析", "ANALYSIS_MODE_UNSUPPORTED")
        # Optional diagnostic reference. A failed/unphysical WFNO stays unknown;
        # it never supplies an acceptance criterion or a fabricated zero Airy size.
        fnumber, airy = None, None
        try:
            value = finite(self.system.MFE.GetOperandValue(self.zos.Editors.MFE.MeritOperandType.WFNO,0,0,0,0,0,0,0,0))
            if value > 0:
                fnumber = value
                airy = 1.22 * primary_wave(self.model)["wavelength_um"] * value
        except Exception:
            pass
        for name in self.settings["analyses"]:
            self.item_deadline=min(self.deadline,time.monotonic()+self.settings["item_timeout_s"])
            self.progress(name,None)
            try:
                value=getattr(self,name)();value["status"]="success"
            except NativeOwnershipError:
                raise  # ownership uncertainty forbids every subsequent native call
            except Exception as error:
                value={"status":"error","code":getattr(error,"code","ANALYSIS_TIMEOUT" if isinstance(error,TimeoutError) else "ANALYSIS_NATIVE_FAILED"),"message":str(error)}
            items[name]=value
        result = {"analysis_schema_version":1,"status":"success" if all(i["status"]=="success" for i in items.values()) else "partial",
                "engine":"Zemax OpticStudio ZOS-API", "started_at":started,"finished_at":utc(),
                "elapsed_ms":(time.monotonic()-clock)*1000,"settings":self.settings,"items":items,
                "model_metadata":{"name":self.model.get("name"),"units":self.model["units"],"configuration":self.model["configuration"],
                                  "fields":self.model["fields"],"wavelengths":self.model["wavelengths"],
                                  "working_f_number":fnumber,"airy_radius_um":airy,
                                  "airy_method":"1.22 * primary wavelength * native WFNO; paraxial diagnostic reference only"},
                **suite_verdict(items,self.settings,self.model)}
        if len(json.dumps(result,ensure_ascii=False,allow_nan=False).encode("utf-8")) > MAX_RESULT_BYTES:
            fail("完整分析结果超过单任务 8 MiB 预算", "ANALYSIS_BUDGET_EXCEEDED")
        return result


def analyze_session(session, model, settings, progress=None):
    """Caller owns original project's authorization and the native operation lock."""
    if getattr(session,"native_cleanup_error",None):
        raise NativeOwnershipError(session.native_cleanup_error)
    return NativeSuite(session,model,settings_for(model,settings),progress).execute()


def analyze_native(model, settings, progress=None):
    from core.zos_session import ZOSSession
    from core.workbench_trace import eligibility
    from tools.project_manager import get_active_project_name
    from tools.system_tools import get_current_design_proposal
    session=ZOSSession._instance
    project=get_active_project_name()
    if get_current_design_proposal().get("user_confirmed_to_simulate") is not True:
        fail("当前项目提案未确认","PROPOSAL_NOT_CONFIRMED")
    if not session or not session.is_connected or session.model_project!=project:
        fail("当前项目与模型归属不同","MODEL_PROJECT_MISMATCH")
    if session.last_recovery_error:
        fail("原生模型恢复状态不可信","WORKBENCH_STATE_UNCERTAIN")
    return analyze_session(session,model,settings,progress)
