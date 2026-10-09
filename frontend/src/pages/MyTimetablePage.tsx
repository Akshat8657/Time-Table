import { useState } from 'react'
import { CalendarRange, Info } from 'lucide-react'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { ExportButtons } from '../components/ExportButtons'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { TimetableGrid } from '../components/TimetableGrid'
import { WeekBar } from '../components/WeekBar'
import { formatStamp, mondayOf, todayIso } from '../lib/helpers'
import { useNotifications } from '../lib/notifications'
import type { WeekView } from '../lib/types'
import { useApi } from '../lib/useApi'

/** A teacher's own weekly timetable: only what the HOD has published - combined sessions and co-taught lectures included. */
export default function MyTimetablePage() {
  const [monday, setMonday] = useState(mondayOf(todayIso()))
  const { arrivals } = useNotifications()
  const { data, error, loading, reload } = useApi<WeekView>(`/views/my/week?week_of=${monday}&_=${arrivals}`)

  return (
    <>
      <PageHeader
        title="My Timetable"
        subtitle="Your weekly timetable as published by your HOD, with combined sessions, parallel electives, co-taught lectures and extra lectures. Holidays and events are marked on their day."
        badge="Phase 4"
      />
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <WeekBar monday={monday} onChange={setMonday} />
        <ExportButtons path="/exports/my" stem="My_Timetable" disabled={!data?.published} />
      </div>

      {error ? (
        <Alert tone="danger">
          {error.message}{' '}
          <button type="button" onClick={reload} className="font-semibold underline">Try again</button>
        </Alert>
      ) : loading && !data ? (
        <div className="space-y-3">
          <Skeleton className="h-12" />
          <Skeleton className="h-96" />
        </div>
      ) : data && !data.term ? (
        <EmptyState icon={CalendarRange} title="No active term yet">The timetable appears here after the term is set and published.</EmptyState>
      ) : data && !data.published ? (
        <Panel title="Not published yet" icon={Info}>
          <EmptyState icon={CalendarRange} title="Your timetable is not published yet">
            Your HOD is still preparing it. As soon as it is published you will see it here and get a notification.
          </EmptyState>
        </Panel>
      ) : data ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-[13px] text-muted">
            <Badge tone="brand">{data.stats.lectures} lectures · {data.stats.hours} hours this week</Badge>
            {data.stats.extra > 0 && <Badge tone="info">{data.stats.extra} extra {data.stats.extra === 1 ? 'lecture' : 'lectures'}</Badge>}
            {data.stats.cancelled > 0 && <Badge tone="success">{data.stats.cancelled} not happening (holiday / event)</Badge>}
            {data.last_published_at && <span>Published {formatStamp(data.last_published_at)}</span>}
          </div>
          {data.cells.length === 0 ? (
            <Alert tone="info">No lecture is assigned to you in the published timetable{data.stats.extra === 0 ? '' : ' for this week'}.</Alert>
          ) : null}
          <TimetableGrid grid={data.grid} days={data.days} cells={data.cells} mode="teacher" caption="Your weekly timetable: periods in the rows, days in the columns" />
        </div>
      ) : null}
    </>
  )
}
