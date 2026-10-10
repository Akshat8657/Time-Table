import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { CalendarRange, GraduationCap, LayoutGrid, TriangleAlert, Users } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { ExportButtons } from '../components/ExportButtons'
import { EmptyState, PageHeader, Skeleton } from '../components/Panel'
import { SchoolBar } from '../components/SchoolBar'
import { SelectField } from '../components/Select'
import { Tabs } from '../components/Tabs'
import { MasterGrid, TimetableGrid } from '../components/TimetableGrid'
import { WeekBar } from '../components/WeekBar'
import { DAY_NAMES, formatStamp, mondayOf, todayIso } from '../lib/helpers'
import { ROLE_PREFIX } from '../lib/nav'
import { useSchoolScope } from '../lib/scope'
import { useTerm } from '../lib/term'
import type { MasterView, PublishStatus, ViewOptions, WeekView } from '../lib/types'
import { useApi } from '../lib/useApi'

type Tab = 'class' | 'teacher' | 'master'

function PublishedNote({ view }: { view: WeekView | MasterView }) {
  const pubs = view.publications ?? []
  if (!view.published) return null
  return (
    <p className="text-[13px] text-muted">
      {pubs.length === 1 ? `Published version ${pubs[0].version}` : `${pubs.length} classes published`}
      {view.last_published_at ? ` · latest ${formatStamp(view.last_published_at)}` : ''}
    </p>
  )
}

/** HOD / admin: the published timetable by class, by teacher, or the whole school with filters. Always the LIVE timetable - never a draft. */
export default function TimetablesPage() {
  const { user } = useAuth()
  const scope = useSchoolScope()
  const { active, ready: termReady } = useTerm()
  const prefix = user ? ROLE_PREFIX[user.role] : ''
  const [params, setParams] = useSearchParams()
  const tab = (params.get('tab') as Tab) || 'master'
  const setTab = (t: Tab) => setParams((p) => { p.set('tab', t); return p }, { replace: true })

  const enabled = scope.ready && !!scope.schoolId && !!active
  const options = useApi<ViewOptions>(enabled ? scope.path('/views/options') : null)
  const pubStatus = useApi<PublishStatus>(enabled ? scope.path('/publish/status') : null)

  const [classId, setClassId] = useState('')
  const [teacherId, setTeacherId] = useState('')
  const [mClass, setMClass] = useState('')
  const [mTeacher, setMTeacher] = useState('')
  const [day, setDay] = useState('')
  const [when, setWhen] = useState<'week' | 'today'>('week')
  const [monday, setMonday] = useState(mondayOf(todayIso()))

  // keep a valid choice when the school (or the list) changes
  useEffect(() => {
    if (!options.data) return
    if (!options.data.classes.some((c) => c.id === classId)) setClassId(options.data.classes.find((c) => c.published)?.id ?? options.data.classes[0]?.id ?? '')
    if (!options.data.teachers.some((t) => t.id === teacherId)) setTeacherId(options.data.teachers.find((t) => t.lectures > 0)?.id ?? options.data.teachers[0]?.id ?? '')
    if (mClass && !options.data.classes.some((c) => c.id === mClass)) setMClass('')
    if (mTeacher && !options.data.teachers.some((t) => t.id === mTeacher)) setMTeacher('')
  }, [options.data]) // eslint-disable-line react-hooks/exhaustive-deps

  const classPath = enabled && tab === 'class' && classId ? `/views/class/${classId}?week_of=${monday}` : null
  const teacherPath = enabled && tab === 'teacher' && teacherId ? `/views/teacher/${teacherId}?week_of=${monday}` : null
  const masterPath = useMemo(() => {
    if (!enabled || tab !== 'master') return null
    const q = [`scope=${when}`, when === 'week' ? `week_of=${monday}` : `on=${todayIso()}`]
    if (mClass) q.push(`semester_id=${mClass}`)
    if (mTeacher) q.push(`teacher_id=${mTeacher}`)
    if (day && when === 'week') q.push(`day=${day}`)
    return scope.path(`/views/master?${q.join('&')}`)
  }, [enabled, tab, when, monday, mClass, mTeacher, day, scope])
  const classView = useApi<WeekView>(classPath)
  const teacherView = useApi<WeekView>(teacherPath)
  const masterView = useApi<MasterView>(masterPath)

  if (termReady && !active) {
    return (
      <>
        <PageHeader title="Timetables" subtitle="The published timetable by class, by teacher and for the whole school." />
        <Alert tone="warning" title="Choose the active term first">
          <Link to={`${prefix}/active-term`} className="font-semibold underline">Open Active Term</Link> and pick one.
        </Alert>
      </>
    )
  }

  const unpublished = (pubStatus.data?.summary.unpublished ?? 0)
  const teachers = options.data?.teachers ?? []
  const classOptions = (options.data?.classes ?? []).map((c) => ({ value: c.id, label: c.published ? c.label : `${c.label} (not published)` }))
  const teacherOptions = teachers.map((t) => ({ value: t.id, label: `${t.name}${t.lectures ? ` · ${t.lectures} lectures` : ' · no lectures'}` }))
  const chosenClass = options.data?.classes.find((c) => c.id === classId)
  const chosenTeacher = teachers.find((t) => t.id === teacherId)
  const stem = (s: string) => s.replace(/[^A-Za-z0-9]+/g, '_')

  return (
    <>
      <PageHeader
        title="Timetables"
        subtitle="What teachers see right now: the published timetable, class by class, teacher by teacher, or the whole school with filters. Download any view as a print-friendly PDF or Excel file."
      />
      <SchoolBar />

      {unpublished > 0 && (
        <Alert tone="warning" className="mb-5" title={`${unpublished} ${unpublished === 1 ? 'class has' : 'classes have'} unpublished changes`}>
          The timetable below is the LIVE one. A newer primary draft is waiting.{' '}
          <Link to={`${prefix}/publish`} className="font-semibold underline">Open Publish</Link>
        </Alert>
      )}

      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <Tabs
          label="View"
          value={tab}
          onChange={setTab}
          items={[
            { value: 'master', label: 'School master', icon: <LayoutGrid className="size-4" /> },
            { value: 'class', label: 'By class', icon: <GraduationCap className="size-4" /> },
            { value: 'teacher', label: 'By teacher', icon: <Users className="size-4" /> },
          ]}
        />
        {(tab !== 'master' || when === 'week') && <WeekBar monday={monday} onChange={setMonday} />}
      </div>

      {options.error ? (
        <Alert tone="danger">{options.error.message}</Alert>
      ) : options.loading && !options.data ? (
        <Skeleton className="h-96" />
      ) : options.data && !options.data.published ? (
        <EmptyState icon={CalendarRange} title="Nothing is published yet">
          Teachers see nothing until you publish. <Link to={`${prefix}/publish`} className="font-semibold text-brand-text underline">Open Publish</Link>
        </EmptyState>
      ) : (
        <>
          {tab === 'class' && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-end justify-between gap-3">
                <SelectField label="Class" className="w-72 max-w-full" value={classId} onChange={setClassId} options={classOptions} />
                <ExportButtons path={`/exports/class/${classId}`} stem={`Timetable_${stem(chosenClass?.label ?? 'class')}`} disabled={!classView.data?.published || !chosenClass?.published} />
              </div>
              {classView.error ? <Alert tone="danger">{classView.error.message}</Alert> : classView.loading && !classView.data ? <Skeleton className="h-96" /> : classView.data ? (
                !chosenClass?.published ? (
                  <Alert tone="info">{chosenClass?.label} is not published yet.</Alert>
                ) : (
                  <>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone="brand">{classView.data.stats.lectures} lectures · {classView.data.stats.hours} hours this week</Badge>
                      {classView.data.stats.cancelled > 0 && <Badge tone="success">{classView.data.stats.cancelled} not happening</Badge>}
                      <PublishedNote view={classView.data} />
                    </div>
                    <TimetableGrid grid={classView.data.grid} days={classView.data.days} cells={classView.data.cells} mode="class" offDays={classView.data.off_days?.[classId]} caption={`Weekly timetable of ${classView.data.owner?.name ?? 'the class'}`} />
                  </>
                )
              ) : null}
            </div>
          )}

          {tab === 'teacher' && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-end justify-between gap-3">
                <SelectField label="Teacher" className="w-80 max-w-full" value={teacherId} onChange={setTeacherId} options={teacherOptions} />
                <ExportButtons path={`/exports/teacher/${teacherId}`} stem={`Timetable_${stem(chosenTeacher?.name ?? 'teacher')}`} disabled={!teacherView.data?.published} />
              </div>
              {teacherView.error ? <Alert tone="danger">{teacherView.error.message}</Alert> : teacherView.loading && !teacherView.data ? <Skeleton className="h-96" /> : teacherView.data ? (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="brand">{teacherView.data.stats.lectures} lectures · {teacherView.data.stats.hours} hours this week</Badge>
                    {teacherView.data.stats.extra > 0 && <Badge tone="info">{teacherView.data.stats.extra} extra</Badge>}
                    {teacherView.data.stats.cancelled > 0 && <Badge tone="success">{teacherView.data.stats.cancelled} not happening</Badge>}
                    <PublishedNote view={teacherView.data} />
                  </div>
                  {teacherView.data.cells.length === 0 && <Alert tone="info">{chosenTeacher?.name} has no lecture in the published timetable{teacherView.data.stats.extra ? ' this week' : ''}.</Alert>}
                  <TimetableGrid grid={teacherView.data.grid} days={teacherView.data.days} cells={teacherView.data.cells} mode="teacher" caption={`Weekly timetable of ${teacherView.data.owner?.name ?? 'the teacher'}`} />
                </>
              ) : null}
            </div>
          )}

          {tab === 'master' && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-end gap-3 rounded-2xl border border-line bg-surface px-4 py-3 shadow-card" role="group" aria-label="Filters">
                <SelectField label="Show" className="w-40" value={when} onChange={(v) => setWhen(v as 'week' | 'today')} options={[{ value: 'week', label: 'Whole week' }, { value: 'today', label: 'Today only' }]} />
                <SelectField label="Class" className="w-56" value={mClass} onChange={setMClass} placeholder="All classes" options={classOptions} />
                <SelectField label="Teacher" className="w-64" value={mTeacher} onChange={setMTeacher} placeholder="All teachers" options={teacherOptions} />
                <SelectField
                  label="Day"
                  className="w-40"
                  value={when === 'today' ? '' : day}
                  onChange={setDay}
                  disabled={when === 'today'}
                  placeholder="All days"
                  options={DAY_NAMES.slice(0, masterView.data?.days.length ?? 6).map((n, i) => ({ value: String(i + 1), label: n }))}
                />
                <div className="ml-auto pb-0.5">
                  <ExportButtons
                    path="/exports/master"
                    stem={`School_Master${mClass || mTeacher || day ? '_filtered' : ''}`}
                    params={[scope.isAdmin && scope.schoolId ? `school_id=${scope.schoolId}` : '', mClass ? `semester_id=${mClass}` : '', mTeacher ? `teacher_id=${mTeacher}` : '', day && when === 'week' ? `day=${day}` : ''].filter(Boolean).join('&')}
                    disabled={!masterView.data?.published}
                  />
                </div>
              </div>
              {masterView.error ? <Alert tone="danger">{masterView.error.message}</Alert> : masterView.loading && !masterView.data ? <Skeleton className="h-96" /> : masterView.data ? (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="brand">{masterView.data.stats.lectures} lectures · {masterView.data.stats.hours} hours{when === 'today' ? ' today' : ' shown'}</Badge>
                    {masterView.data.stats.cancelled > 0 && <Badge tone="success">{masterView.data.stats.cancelled} not happening</Badge>}
                    {masterView.data.classes.some((c) => !c.published) && <Badge tone="warning"><TriangleAlert className="size-3" aria-hidden="true" /> some classes are not published</Badge>}
                    <PublishedNote view={masterView.data} />
                  </div>
                  <MasterGrid view={masterView.data} />
                </>
              ) : null}
            </div>
          )}
        </>
      )}
    </>
  )
}
