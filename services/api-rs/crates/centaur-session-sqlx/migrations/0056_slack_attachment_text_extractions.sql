create table if not exists slack_attachment_text_extractions (
    extraction_id bigint generated always as identity primary key,
    channel_id text not null,
    message_ts text not null,
    slack_file_id text not null,
    source_content_sha256 text,
    extractor_version text not null,
    status text not null
        check (status in ('succeeded', 'unsupported', 'failed')),
    text_content text not null default '',
    metadata jsonb not null default '{}'::jsonb,
    last_error text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (channel_id, message_ts, slack_file_id),
    foreign key (channel_id, message_ts, slack_file_id)
        references slack_sync_message_attachments (
            channel_id,
            message_ts,
            slack_file_id
        )
        on delete cascade
);

create index if not exists idx_slack_attachment_text_extractions_version
    on slack_attachment_text_extractions (extractor_version, updated_at);

grant select on slack_attachment_text_extractions
    to centaur_slack_reader, centaur_readonly;

alter table slack_attachment_text_extractions enable row level security;

drop policy if exists centaur_slack_attachment_extractions_reader_select
    on slack_attachment_text_extractions;
create policy centaur_slack_attachment_extractions_reader_select
    on slack_attachment_text_extractions
    for select
    to centaur_slack_reader
    using (
        channel_id = centaur_current_slack_channel_id()
    );

drop policy if exists centaur_readonly_slack_attachment_extractions_select
    on slack_attachment_text_extractions;
create policy centaur_readonly_slack_attachment_extractions_select
    on slack_attachment_text_extractions
    for select
    to centaur_readonly
    using (
        exists (
            select 1
            from slack_sync_channels channels
            where channels.channel_id = slack_attachment_text_extractions.channel_id
        )
    );
