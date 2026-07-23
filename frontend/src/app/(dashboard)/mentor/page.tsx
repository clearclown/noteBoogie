'use client'

import { useState } from 'react'
import { MessageCircle, Presentation, Scale } from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { MentorChat } from '@/components/mentor/MentorChat'
import { MentorMemoryPanel } from '@/components/mentor/MentorMemoryPanel'
import { MentorPersonaDialog } from '@/components/mentor/MentorPersonaDialog'
import { MentorSlidesTab } from '@/components/mentor/MentorSlidesTab'
import { MentorWeightsTab } from '@/components/mentor/MentorWeightsTab'
import type { SlideReview } from '@/lib/api/mentor'
import { useTranslation } from '@/lib/hooks/use-translation'

export default function MentorPage() {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<'chat' | 'slides' | 'weights'>('chat')
  const [chatDraft, setChatDraft] = useState<string | undefined>(undefined)

  // スライドレビュー → 相談タブへの深掘り引き継ぎ（MENTOR_UI_DESIGN §11）
  const discussReview = (review: SlideReview) => {
    const failing = review.axes.filter((axis) => !axis.passed)
    setChatDraft(
      t('mentor.discussPrefill', {
        filename: review.filename,
        overall: review.overall.toFixed(1),
        fix: review.top_fix ?? failing[0]?.issues[0]?.text ?? review.summary ?? '',
      })
    )
    setActiveTab('chat')
  }

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <div className="space-y-6 px-6 py-6">
          <header className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-1">
              <h1 className="text-2xl font-semibold tracking-tight">{t('mentor.title')}</h1>
              <p className="text-muted-foreground">{t('mentor.subtitle')}</p>
            </div>
            <div className="flex shrink-0 gap-2">
              <MentorPersonaDialog />
              <MentorMemoryPanel />
            </div>
          </header>

          <Tabs
            value={activeTab}
            onValueChange={(value) => setActiveTab(value as typeof activeTab)}
          >
            <TabsList>
              <TabsTrigger value="chat" className="gap-1.5">
                <MessageCircle className="h-4 w-4" />
                {t('mentor.tabChat')}
              </TabsTrigger>
              <TabsTrigger value="slides" className="gap-1.5">
                <Presentation className="h-4 w-4" />
                {t('mentor.tabSlides')}
              </TabsTrigger>
              <TabsTrigger value="weights" className="gap-1.5">
                <Scale className="h-4 w-4" />
                {t('mentor.tabWeights')}
              </TabsTrigger>
            </TabsList>
            <TabsContent value="chat" className="mt-4">
              <MentorChat draft={chatDraft} />
            </TabsContent>
            <TabsContent value="slides" className="mt-4">
              <MentorSlidesTab onDiscuss={discussReview} />
            </TabsContent>
            <TabsContent value="weights" className="mt-4">
              <MentorWeightsTab />
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </AppShell>
  )
}
